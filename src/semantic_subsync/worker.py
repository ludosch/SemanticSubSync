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
     side               the subtitle is left as is, the correction is '<video>.resync.<lang...>.default.srt':
                        Jellyfin shows it as a track titled "Resync" and picks it by default (a forced
                        subtitle's correction gets no "default" flag).
     When no correction is needed (any more), the downloaded subtitle gets its name back and the
     extra file is removed.
The sentence model is SEMSYNC_MODEL (static by default, or minilm). A model chosen by hand for one
subtitle (`one --model`) is kept in state.db and used again for that subtitle on later runs, until
another choice (`--model default` goes back to SEMSYNC_MODEL).

Usage: semantic-subsync-worker run                          process the queue forever
       semantic-subsync-worker one [--force] [--model NAME] VIDEO SUBTITLE
                                                            process one pair now; NAME: static, minilm
                                                            or default (see above)
       semantic-subsync-worker backfill [ROOT]              enqueue every external .srt next to its video
       semantic-subsync-worker status [--fields] [WORD...] what state.db knows, for the paths containing every WORD
       semantic-subsync-worker history WORD...              every logged decision about the paths containing every
                                                            WORD, e.g. history TITLE S01E02 fr
       semantic-subsync-worker prepare                      download and load the sentence model (SEMSYNC_MODEL) now,
                                                            so the first subtitle does not wait for it
"""
import gc, json, os, sys, time, traceback
from semantic_subsync import __version__, core, media
from semantic_subsync.state import FIELDS, State, sha256, video_stamp

BASE = os.environ.get("SEMSYNC_DIR", "/data/.semsync")
QUEUE, FAILED = f"{BASE}/queue", f"{BASE}/failed"
LOG = f"{BASE}/semsync.log"
# the log is the history of every decision; past this size its oldest entries are deleted
LOG_MAX_BYTES = int(float(os.environ.get("SEMSYNC_LOG_MAX_MB", "10")) * 1024 * 1024)
DB = f"{BASE}/state.db"
POLL = 30
OUTPUT = os.environ.get("SEMSYNC_OUTPUT", "replace")        # "replace" or "side"
# lines the video has no room for (a credit, a recap or a scene it lacks): "drop" or "keep"
EXTRA_LINES = os.environ.get("SEMSYNC_EXTRA_LINES", core.P["extra_lines"])
REPLACED, RESYNC = "replaced", "resync"
OWN_TAGS = (REPLACED, RESYNC)


def log(rec):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    line = json.dumps(rec, ensure_ascii=False)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    trim_log()


def trim_log(max_bytes=None):
    """Past SEMSYNC_LOG_MAX_MB, delete the oldest entries: the newest half of the limit is kept,
    cut at a line boundary. No archive copy is made. 0 = no limit."""
    max_bytes = LOG_MAX_BYTES if max_bytes is None else max_bytes
    if not max_bytes or os.path.getsize(LOG) <= max_bytes:
        return
    with open(LOG, "rb") as f:
        f.seek(-(max_bytes // 2), os.SEEK_END)
        f.readline()                                   # the first line read is a partial one
        kept = f.read()
    with open(LOG + ".tmp", "wb") as f:
        f.write(kept)
    os.replace(LOG + ".tmp", LOG)


def tagged_path(video, sub, tag):
    """'Show - S01E01.fr.hi.srt' -> 'Show - S01E01.<tag>.fr.hi.srt'. Jellyfin reads what follows
    the video's name: the language and hi/forced flags, the other words being the track title."""
    stem = os.path.splitext(video)[0]
    if sub.startswith(stem + "."):
        return stem + f".{tag}" + sub[len(stem):]
    return os.path.splitext(sub)[0] + f".{tag}.srt"


def side_path(video, sub):
    """'Show - S01E01.fr.srt' -> 'Show - S01E01.resync.fr.default.srt': the "default" flag makes Jellyfin
    pick the correction over the download, which keeps its name. A forced subtitle gets no flag: it
    would be chosen over the full subtitles."""
    path = tagged_path(video, sub, RESYNC)
    toks = os.path.basename(path).lower().split(".")[1:-1]
    if "forced" in toks or "default" in toks:
        return path
    return os.path.splitext(path)[0] + ".default.srt"


def is_own(path):
    """A file this worker wrote (or kept aside): never a job of its own."""
    toks = os.path.basename(path).lower().split(".")
    return any(t in toks[1:-1] for t in OWN_TAGS)


def settings(model):
    return {"extra_lines": EXTRA_LINES, "min_coverage": core.MIN_COVERAGE, "model": model,
            "min_sim": core.MODELS[model]["min_sim"]}


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


def process(video, sub, origin="manual", force=False, score=None, state=None, model=None):
    """`model`: a model chosen by hand for this subtitle (kept for later runs), "default" to
    drop such a choice, None to keep what state.db says."""
    if model not in (None, "default"):
        core.check_model(model)
    t0 = time.time()
    rec = {"origin": origin, "video": video, "sub": sub}
    if not (os.path.isfile(video) and os.path.isfile(sub)) or not sub.lower().endswith(".srt") or is_own(sub):
        return log({**rec, "status": "skipped", "reason": "missing file or not a downloaded .srt"})
    own_state = state is None
    state = state or State(DB)
    try:
        return _process(video, sub, rec, t0, force, score, state, model)
    finally:
        if own_state:
            state.close()


def kept_output(row, sub, side):
    """The correction recorded for this subtitle is still there, unmodified, under the name this
    version writes: otherwise the subtitle is processed again (a deleted or renamed correction is
    written back)."""
    out = row["output_path"]
    return out is None or (out in (sub, side) and os.path.isfile(out) and sha256(out) == row["output_sha256"])


def _process(video, sub, rec, t0, force, score, state, model):
    replaced, side = tagged_path(video, sub, REPLACED), side_path(video, sub)
    row = state.get(sub)
    choice = (row or {}).get("model_choice") if model is None else (None if model == "default" else model)
    use = choice or core.DEFAULT_MODEL
    cur = sha256(sub)
    # Where is the downloaded subtitle? In replace mode, `sub` may hold the worker's own correction and
    # the download sits in the .replaced file; a new download overwrites `sub` (another hash).
    ours = bool(row and row["output_sha256"] == cur and row["output_path"] == sub)
    source = replaced if ours and os.path.isfile(replaced) else sub
    src_sha = sha256(source)
    vsize, vmtime = video_stamp(video)
    if (not force and row and row["status"] != "error" and row["input_sha256"] == src_sha
            and (row["video_size"], row["video_mtime"]) == (vsize, vmtime) and row["output_mode"] == OUTPUT
            and (model is None or (row["model"], row["model_choice"]) == (use, choice))
            and cur in (row["input_sha256"], row["output_sha256"])
            and kept_output(row, sub, side)):
        return log({**rec, "status": "unchanged", "last": row["status"], "processed_at": row["processed_at"]})

    lang, kind = media.language_key(media.language_of(sub)), media.kind_of(sub)
    entry = {**rec, "lang": lang, "kind": kind, "input_sha256": src_sha, "input_size": os.path.getsize(source),
             "video_size": vsize, "video_mtime": vmtime, "output_mode": OUTPUT, "engine_version": __version__,
             "model": use, "model_choice": choice, "settings": settings(use), "score": score}

    def settle(status, out=None, **info):
        """Put the files in their final state; record and log the decision."""
        cleaned = []
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
        log({k: v for k, v in full.items() if v is not None} | ({"removed": cleaned} if cleaned else {}))

    tracks = media.probe_subtitles(video)
    twin = media.embedded_twin(tracks, sub)
    if twin:
        return settle("redundant", reason=f"embedded #{twin['index']} {twin['lang']} {twin['kind']} {twin['title']}".strip())
    found = media.best_reference(video, tracks=tracks)
    if found is None:
        return settle("no_reference")
    ref, ref_desc = found
    status, out, st = core.resync(media.read_srt(source), ref, p={"extra_lines": EXTRA_LINES}, model=use)
    info = {"reference": ref_desc, "coverage": st.get("coverage"), "segments": st.get("segments"),
            "max_abs_offset": st.get("max_abs_offset"), "dropped": st.get("dropped"), "seg": segments(st)}
    if status == "unsure":
        return settle("unsure", **info, reason=st.get("status"))
    if status == "in_sync":
        return settle("in_sync", **info)
    settle("corrected", out, **info)


def segments(st):
    """The correction, segment by segment: from / to (s, in the subtitle), offset at the start of
    the segment (s) and frame-rate drift (ppm; 23.976 -> 25 fps is about -40000)."""
    return [{"from": round(s["t0"], 2), "to": round(s["t1"], 2),
             "offset": round(s["a"] + s["drift_ppm"] * 1e-6 * s["t0"], 2), "drift_ppm": round(s["drift_ppm"])}
            for s in st.get("seg", [])] or None


def unload_model():
    """A model takes 0.2 to 0.5 GB: free it while the queue is empty."""
    if core._models:
        core._models.clear(); core._cache.clear(); gc.collect()


def run():
    os.makedirs(QUEUE, exist_ok=True); os.makedirs(FAILED, exist_ok=True)
    log({"status": "worker_started", "version": __version__, "output": OUTPUT, "extra_lines": EXTRA_LINES,
         "model": core.DEFAULT_MODEL, "model_dir": os.environ.get("SEMSYNC_MODEL_DIR")})
    state = State(DB)
    while True:
        jobs = sorted((os.path.join(QUEUE, j) for j in os.listdir(QUEUE) if j.endswith(".job")), key=os.path.getmtime)
        if not jobs:
            unload_model(); time.sleep(POLL); continue
        for job in jobs:
            try:
                j = json.load(open(job, encoding="utf-8"))
                process(j["video"], j["sub"], j.get("origin", "bazarr"), j.get("force", False), j.get("score"), state,
                        j.get("model"))
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


def matches(path, terms):
    """True when the path contains every term, whatever the case: `TITLE S01E02` does not
    match the S01E02 of another series."""
    path = (path or "").lower()
    return all(t.lower() in path for t in terms)


def status(args):
    if "--fields" in args:
        for k, v in FIELDS.items():
            print(f"{k:16} {v}")
        return
    terms = [a for a in args if not a.startswith("-")]
    rows = [r for r in State(DB).all() if matches(r["sub"], terms)]
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(f"{r['processed_at']}  {r['status']:12} {r['sub']}")
    print(json.dumps(counts))


def history(terms):
    """Every log entry about the subtitles whose path contains all `terms`, grouped by subtitle
    (full path first, so the series, season and language are explicit), oldest first."""
    if not os.path.exists(LOG):
        return
    groups = {}
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("sub") and matches(r["sub"], terms):
                groups.setdefault(r["sub"], []).append(r)
    for sub, recs in groups.items():
        print(sub)
        for r in recs:
            facts = [f"{k}={r[k]}" for k in ("origin", "model", "reference", "coverage", "segments", "max_abs_offset",
                                             "dropped", "reason", "engine_version", "secs") if r.get(k) is not None]
            print(f"  {r['ts']}  {r['status']}" + ("\n      " + "  ".join(facts) if facts else ""))
            for s in r.get("seg") or []:
                print(f"      {s['from']:8.1f} s -> {s['to']:8.1f} s  offset {s['offset']:+.2f} s"
                      + (f"  drift {s['drift_ppm']} ppm" if s.get("drift_ppm") else ""))
            for p in r.get("removed") or []:
                print(f"      removed {os.path.basename(p)}")
        print()
    if len(groups) > 1:
        print(f"{len(groups)} subtitles match: add words to narrow down (series, season, language)")


def main():
    if hasattr(os, "nice"):         # POSIX only
        os.nice(19)                 # Jellyfin keeps priority on the CPU
    args = sys.argv[1:] or ["run"]
    cmd, rest = args[0], args[1:]
    if cmd == "run":
        run()
    elif cmd == "one":
        os.makedirs(BASE, exist_ok=True)
        force = "--force" in rest
        model = None
        if "--model" in rest:
            i = rest.index("--model"); model = rest[i + 1]; del rest[i:i + 2]
        video, sub = [a for a in rest if a != "--force"]
        process(video, sub, force=force, model=model)
    elif cmd == "backfill":
        backfill(rest[0] if rest else "/data/media")
    elif cmd == "status":
        status(rest)
    elif cmd == "history" and rest:
        history(rest)
    elif cmd == "prepare":
        t0 = time.time(); core.embed(["ready"], model=core.DEFAULT_MODEL)
        print(json.dumps({"status": "ready", "model": core.DEFAULT_MODEL, "secs": round(time.time() - t0, 1)}))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
