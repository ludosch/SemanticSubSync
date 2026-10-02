#!/usr/bin/env python3
"""Queue worker: re-times subtitles that land next to videos, on a subtitle embedded in the video.

Any program can feed it: a job is a small JSON file {"video": ..., "sub": ...} dropped into
$SEMSYNC_DIR/queue (see integrations/bazarr/enqueue.py for Bazarr). For each job the worker:
  1. skips a subtitle it already processed, unchanged, for the same video (state.db);
  2. skips a subtitle that duplicates an embedded TEXT subtitle of the same language and kind
     (normal / hearing impaired / forced): the video already has it ("redundant");
  3. extracts the embedded text subtitles of the video (forced tracks excluded) and keeps the
     fullest one as reference (any language: an embedded subtitle is assumed to be in sync);
  4. aligns the subtitle on that reference by meaning (core.resync);
  5. when a correction is needed, writes it according to SEMSYNC_OUTPUT:
     replace (default)  the correction takes the subtitle's name and the downloaded subtitle is
                        kept as '<video>.replaced.<lang...>.srt': Jellyfin shows it as an extra
                        track titled "Replaced", to switch back to if the correction is wrong;
     side               the subtitle is left as is, the correction is '<video>.resync.<lang...>.srt'.
     When no correction is needed (any more), the downloaded subtitle gets its name back and the
     extra file is removed.

Usage: semantic-subsync-worker run                          process the queue forever
       semantic-subsync-worker one [--force] VIDEO SUBTITLE process one pair now
       semantic-subsync-worker backfill [ROOT]              enqueue every external .srt next to its video
       semantic-subsync-worker status [--fields] [TEXT]     what state.db knows (TEXT: filter on paths)
"""
import gc, json, os, sys, time, traceback
from semantic_subsync import __version__, core, media
from semantic_subsync.state import FIELDS, State, sha256, video_stamp

BASE = os.environ.get("SEMSYNC_DIR", "/data/.semsync")
QUEUE, FAILED = f"{BASE}/queue", f"{BASE}/failed"
LOG = f"{BASE}/semsync.log"
DB = f"{BASE}/state.db"
POLL = 30
OUTPUT = os.environ.get("SEMSYNC_OUTPUT", "replace")        # "replace" or "side"
# lines the video has no room for (a credit, a recap or a scene it lacks): "drop" or "keep"
EXTRA_LINES = os.environ.get("SEMSYNC_EXTRA_LINES", core.P["extra_lines"])
REPLACED, RESYNC = "replaced", "resync"
LEGACY = "semsync"                                          # side files written by 0.9 and earlier
OWN_TAGS = (REPLACED, RESYNC, LEGACY)


def log(rec):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    line = json.dumps(rec, ensure_ascii=False)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def tagged_path(video, sub, tag):
    """'Show - S01E01.fr.hi.srt' -> 'Show - S01E01.<tag>.fr.hi.srt'. Jellyfin reads what follows
    the video's name: the language and hi/forced flags, the other words being the track title."""
    stem = os.path.splitext(video)[0]
    if sub.startswith(stem + "."):
        return stem + f".{tag}" + sub[len(stem):]
    return os.path.splitext(sub)[0] + f".{tag}.srt"


def side_path(video, sub):
    return tagged_path(video, sub, RESYNC)


def is_own(path):
    """A file this worker wrote (or kept aside): never a job of its own."""
    toks = os.path.basename(path).lower().split(".")
    return any(t in toks[1:-1] for t in OWN_TAGS)


def settings():
    return {"extra_lines": EXTRA_LINES, "min_coverage": core.MIN_COVERAGE,
            "model": os.path.basename(os.environ.get("SEMSYNC_MODEL_DIR", "") or core.MODEL)}


def _write_atomic(path, cues=None, copy_of=None):
    tmp = path + ".tmp"
    if copy_of:
        with open(copy_of, "rb") as src, open(tmp, "wb") as dst:
            dst.write(src.read())
    else:
        core.write(tmp, cues)
    os.chmod(tmp, 0o664)
    os.replace(tmp, path)


def _remove(path):
    if os.path.exists(path):
        os.remove(path); return path
    return None


def process(video, sub, origin="manual", force=False, score=None, state=None):
    t0 = time.time()
    rec = {"origin": origin, "video": video, "sub": sub}
    if not (os.path.isfile(video) and os.path.isfile(sub)) or not sub.lower().endswith(".srt") or is_own(sub):
        return log({**rec, "status": "skipped", "reason": "missing file or not a downloaded .srt"})
    own_state = state is None
    state = state or State(DB)
    try:
        return _process(video, sub, rec, t0, force, score, state)
    finally:
        if own_state:
            state.close()


def _process(video, sub, rec, t0, force, score, state):
    replaced, side = tagged_path(video, sub, REPLACED), tagged_path(video, sub, RESYNC)
    row = state.get(sub)
    cur = sha256(sub)
    # Where is the downloaded subtitle? In replace mode, `sub` may hold our own correction and
    # the download sits in the .replaced file; a new download overwrites `sub` (another hash).
    ours = bool(row and row["output_sha256"] == cur and row["output_path"] == sub)
    source = replaced if ours and os.path.isfile(replaced) else sub
    src_sha = sha256(source)
    vsize, vmtime = video_stamp(video)
    if (not force and row and row["status"] != "error" and row["input_sha256"] == src_sha
            and (row["video_size"], row["video_mtime"]) == (vsize, vmtime) and row["output_mode"] == OUTPUT
            and cur in (row["input_sha256"], row["output_sha256"])):
        return log({**rec, "status": "unchanged", "last": row["status"], "processed_at": row["processed_at"]})

    lang, kind = media.language_key(media.language_of(sub)), media.kind_of(sub)
    entry = {**rec, "lang": lang, "kind": kind, "input_sha256": src_sha, "input_size": os.path.getsize(source),
             "video_size": vsize, "video_mtime": vmtime, "output_mode": OUTPUT, "engine_version": __version__,
             "settings": settings(), "score": score}

    def settle(status, out=None, **info):
        """Put the files in their final state; record and log the decision."""
        cleaned = [p for p in [_remove(tagged_path(video, sub, LEGACY))] if p]
        if out is None:
            if source == replaced:
                os.replace(replaced, sub)          # back to the download: no correction any more
            else:
                cleaned.append(_remove(replaced))
            cleaned.append(_remove(side))
            outp = None
        elif OUTPUT == "replace":
            if source == sub:
                _write_atomic(replaced, copy_of=sub)   # the download stays visible as "Replaced"
            _write_atomic(sub, out)
            cleaned.append(_remove(side))
            outp = sub
        else:
            if source == replaced:
                os.replace(replaced, sub)
            else:
                cleaned.append(_remove(replaced))
            _write_atomic(side, out)
            outp = side
        full = {**entry, "status": status, "secs": round(time.time() - t0, 1), **info,
                "output_path": outp, "output_sha256": sha256(outp) if outp else None,
                "replaced_path": replaced if outp == sub else None}
        state.put(full)
        cleaned = [p for p in cleaned if p]
        log({k: v for k, v in full.items() if v is not None and k not in ("settings", "input_sha256", "output_sha256")}
            | ({"removed": cleaned} if cleaned else {}))

    tracks = media.probe_subtitles(video)
    twin = media.embedded_twin(tracks, sub)
    if twin:
        return settle("redundant", reason=f"embedded #{twin['index']} {twin['lang']} {twin['kind']} {twin['title']}".strip())
    found = media.best_reference(video, tracks=tracks)
    if found is None:
        return settle("no_reference")
    ref, ref_desc = found
    status, out, st = core.resync(media.read_srt(source), ref, p={**core.P, "extra_lines": EXTRA_LINES})
    info = {"reference": ref_desc, "coverage": st.get("coverage"), "segments": st.get("segments"),
            "max_abs_offset": st.get("max_abs_offset"), "dropped": st.get("dropped")}
    if status == "refused":
        return settle("refused", **info, reason=st.get("status"))
    if status == "in_sync":
        return settle("in_sync", **info)
    settle("corrected", out, **info)


def unload_model():
    """The model takes ~0.5 GB: free it while the queue is empty."""
    if core._model is not None:
        core._model = None; core._cache.clear(); gc.collect()


def run():
    os.makedirs(QUEUE, exist_ok=True); os.makedirs(FAILED, exist_ok=True)
    log({"status": "worker_started", "version": __version__, "output": OUTPUT, "extra_lines": EXTRA_LINES,
         "model": os.environ.get("SEMSYNC_MODEL_DIR")})
    state = State(DB)
    while True:
        jobs = sorted((os.path.join(QUEUE, j) for j in os.listdir(QUEUE) if j.endswith(".job")), key=os.path.getmtime)
        if not jobs:
            unload_model(); time.sleep(POLL); continue
        for job in jobs:
            try:
                j = json.load(open(job, encoding="utf-8"))
                process(j["video"], j["sub"], j.get("origin", "bazarr"), j.get("force", False), j.get("score"), state)
                os.remove(job)
            except Exception as e:
                log({"status": "error", "job": os.path.basename(job), "error": repr(e), "trace": traceback.format_exc()[-800:]})
                os.replace(job, os.path.join(FAILED, os.path.basename(job)))


def backfill(root):
    """Queue every external .srt that sits next to its video (same file name stem). Subtitles
    already processed and unchanged are skipped quickly by the worker (state.db)."""
    os.makedirs(QUEUE, exist_ok=True); n = 0
    for d, _, files in os.walk(root):
        videos = [f for f in files if f.lower().endswith(media.VIDEO_EXT)]
        for f in sorted(files):
            if not f.lower().endswith(".srt") or is_own(f):
                continue
            v = next((x for x in videos if f.startswith(os.path.splitext(x)[0] + ".")), None)
            if v:
                job = {"video": os.path.join(d, v), "sub": os.path.join(d, f), "origin": "backfill"}
                with open(os.path.join(QUEUE, f"backfill-{n:05d}.job"), "w", encoding="utf-8") as fh:
                    json.dump(job, fh, ensure_ascii=False)
                n += 1
    log({"status": "backfill_queued", "root": root, "jobs": n})


def status(args):
    if "--fields" in args:
        for k, v in FIELDS.items():
            print(f"{k:16} {v}")
        return
    rows = State(DB).all(next((a for a in args if not a.startswith("-")), None))
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(f"{r['processed_at']}  {r['status']:12} {os.path.basename(r['sub'])}")
    print(json.dumps(counts))


def main():
    os.nice(19)                     # Jellyfin keeps priority on the CPU
    args = sys.argv[1:] or ["run"]
    cmd, rest = args[0], args[1:]
    if cmd == "run":
        run()
    elif cmd == "one":
        os.makedirs(BASE, exist_ok=True)
        force = "--force" in rest
        video, sub = [a for a in rest if a != "--force"]
        process(video, sub, force=force)
    elif cmd == "backfill":
        backfill(rest[0] if rest else "/data/media")
    elif cmd == "status":
        status(rest)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
