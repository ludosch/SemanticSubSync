#!/usr/bin/env python3
"""semsync worker: re-times downloaded subtitles on a subtitle embedded in the video.

Bazarr (custom post-processing) drops one job per downloaded subtitle into /data/.semsync/queue
(see enqueue.py). For each job the worker:
  1. extracts the embedded TEXT subtitles of the video (forced tracks excluded) and keeps the
     fullest one as reference (any language: an embedded subtitle is assumed to be in sync);
  2. runs semsync (semantic alignment) of the downloaded subtitle onto that reference;
  3. writes '<video>.semsync.<lang...>.srt' next to the original when a correction is needed.
     The downloaded subtitle itself is never modified. A stale .semsync file is removed when
     the new subtitle needs no correction or cannot be checked.

Usage: worker.py run                 process the queue forever
       worker.py one VIDEO SUBTITLE  process one pair now (test)
       worker.py backfill [ROOT]     enqueue every existing external .srt under ROOT (/data/media)
"""
import gc, json, os, subprocess, sys, tempfile, time, traceback
from semantic_subsync import core as semsync

BASE = os.environ.get("SEMSYNC_DIR", "/data/.semsync")
QUEUE, FAILED = f"{BASE}/queue", f"{BASE}/failed"
LOG = f"{BASE}/semsync.log"
TEXT_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm")
MIN_COVERAGE = 0.25        # below: the reference does not say the same thing -> leave untouched
POLL = 30


def log(rec):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    line = json.dumps(rec, ensure_ascii=False)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def side_path(video, sub):
    """'Show - S01E01.fr.hi.srt' -> 'Show - S01E01.semsync.fr.hi.srt' (Jellyfin: French, title semsync)."""
    stem = os.path.splitext(video)[0]
    if sub.startswith(stem + "."):
        return stem + ".semsync" + sub[len(stem):]
    return os.path.splitext(sub)[0] + ".semsync.srt"


def read_srt(path):
    """semsync.parse, but downloaded subtitles are sometimes cp1252 rather than UTF-8."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return semsync.parse_text(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return semsync.parse_text(raw.decode("utf-8", errors="replace"))


def references(video, workdir):
    """Extract every non-forced embedded text subtitle in ONE read of the file; return [(cues, desc)]."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=index,codec_name,codec_type:stream_tags=language,title:stream_disposition=forced",
                            "-of", "json", video], capture_output=True, text=True, timeout=300)
    streams = json.loads(probe.stdout or "{}").get("streams", [])
    subs = [s for s in streams if s.get("codec_type") == "subtitle" and s.get("codec_name") in TEXT_CODECS
            and not s.get("disposition", {}).get("forced")]
    if not subs:
        return []
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video]
    for s in subs:
        cmd += ["-map", f"0:{s['index']}", "-f", "srt", f"{workdir}/{s['index']}.srt"]
    subprocess.run(cmd, capture_output=True, timeout=1800)
    out = []
    for s in subs:
        p = f"{workdir}/{s['index']}.srt"
        if os.path.exists(p) and os.path.getsize(p) > 0:
            tags = s.get("tags", {})
            out.append((semsync.parse(p), f"#{s['index']} {tags.get('language', 'und')} {tags.get('title', '')}".strip()))
    return out


def process(video, sub, origin="manual"):
    t0 = time.time()
    rec = {"origin": origin, "video": video, "sub": sub}
    side = side_path(video, sub)
    if not (os.path.isfile(video) and os.path.isfile(sub)) or not sub.lower().endswith(".srt") or ".semsync." in sub:
        return log({**rec, "status": "skipped", "reason": "missing file or not a downloaded .srt"})

    def drop_stale(status, **extra):
        if os.path.exists(side):
            os.remove(side); extra["removed_stale"] = side
        log({**rec, "status": status, "secs": round(time.time() - t0, 1), **extra})

    with tempfile.TemporaryDirectory() as wd:
        refs = references(video, wd)
    refs = [r for r in refs if len(r[0]) >= 20]
    if not refs:
        return drop_stale("no_reference")
    ref, ref_desc = max(refs, key=lambda r: len(r[0]))      # fullest embedded track
    tgt = read_srt(sub)
    out, st = semsync.sync(tgt, ref)
    info = {"reference": ref_desc, "coverage": st.get("coverage"), "segments": st.get("segments"),
            "max_abs_offset": st.get("max_abs_offset")}
    if out is None or (st.get("coverage") or 0) < MIN_COVERAGE:
        return drop_stale("refused", **info, why=st.get("status"))
    if all(abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6 for a, b in zip(out, tgt)):
        return drop_stale("in_sync", **info)
    tmp = side + ".tmp"
    semsync.write(tmp, out)
    os.chmod(tmp, 0o664)
    os.replace(tmp, side)
    log({**rec, "status": "corrected", "output": side, "secs": round(time.time() - t0, 1), **info,
         "seg": [{"from": s["t0"], "to": s["t1"], "offset": round(s["a"] + s["drift_ppm"] * 1e-6 * s["t0"], 2)}
                 for s in st.get("seg", [])]})


def unload_model():
    """The model takes ~0.5 GB: free it while the queue is empty."""
    if semsync._model is not None:
        semsync._model = None; semsync._cache.clear(); gc.collect()


def run():
    os.makedirs(QUEUE, exist_ok=True); os.makedirs(FAILED, exist_ok=True)
    log({"status": "worker_started", "model": os.environ.get("SEMSYNC_MODEL_DIR")})
    while True:
        jobs = sorted((os.path.join(QUEUE, j) for j in os.listdir(QUEUE) if j.endswith(".job")), key=os.path.getmtime)
        if not jobs:
            unload_model(); time.sleep(POLL); continue
        for job in jobs:
            try:
                j = json.load(open(job, encoding="utf-8"))
                process(j["video"], j["sub"], j.get("origin", "bazarr"))
                os.remove(job)
            except Exception as e:
                log({"status": "error", "job": os.path.basename(job), "error": repr(e), "trace": traceback.format_exc()[-800:]})
                os.replace(job, os.path.join(FAILED, os.path.basename(job)))


def backfill(root):
    """Queue every existing external .srt that sits next to its video (same file name stem)."""
    os.makedirs(QUEUE, exist_ok=True); n = 0
    for d, _, files in os.walk(root):
        videos = [f for f in files if f.lower().endswith(VIDEO_EXT)]
        for f in files:
            if not f.lower().endswith(".srt") or ".semsync." in f:
                continue
            v = next((x for x in videos if f.startswith(os.path.splitext(x)[0] + ".")), None)
            if v:
                job = {"video": os.path.join(d, v), "sub": os.path.join(d, f), "origin": "backfill"}
                with open(os.path.join(QUEUE, f"backfill-{n:05d}.job"), "w", encoding="utf-8") as fh:
                    json.dump(job, fh, ensure_ascii=False)
                n += 1
    log({"status": "backfill_queued", "root": root, "jobs": n})


def main():
    os.nice(19)                     # Jellyfin keeps priority on the CPU
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "one":
        os.makedirs(BASE, exist_ok=True); process(sys.argv[2], sys.argv[3])
    elif cmd == "backfill":
        backfill(sys.argv[2] if len(sys.argv) > 2 else "/data/media")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
