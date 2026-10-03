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
                        kept as '<subtitle>.untouched.srt' ('Movie.fr.hi.untouched.srt'): Jellyfin
                        shows it as an extra track titled "untouched", to switch back to if the
                        correction is wrong, listed after the correction (Jellyfin plays the first
                        of a language, in file name order); with SEMSYNC_KEEP_DOWNLOAD=hidden it is
                        kept as '<subtitle>.orig' instead, a name no player reads. A download kept
                        by an earlier version ('<video>.replaced.<lang...>.srt') or under the other
                        SEMSYNC_KEEP_DOWNLOAD value is renamed the next time its subtitle is seen;
     side               the subtitle is left as is, the correction is '<video>.resync.<lang...>.default.srt':
                        Jellyfin shows it as a track titled "Resync" and picks it by default (a forced
                        subtitle's correction gets no "default" flag).
     When no correction is needed (any more), the downloaded subtitle gets its name back and the
     extra file is removed.
A downloaded subtitle is never deleted: a file the state does not account for (state.db lost,
a file edited by hand) is renamed '<name>.<time>.bak' instead of being overwritten or removed.
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
import argparse, contextlib, json, os, shutil, sys, tempfile, time, traceback
try:
    import fcntl
except ImportError:                 # Windows: the log is written without a lock
    fcntl = None
from semantic_subsync import __version__, core, media
from semantic_subsync.state import FIELDS, State, sha256, video_stamp

BASE = os.environ.get("SEMSYNC_DIR", "/data/.semsync")
QUEUE, FAILED = f"{BASE}/queue", f"{BASE}/failed"
LOG = f"{BASE}/semsync.log"


def _megabytes(value):
    try:
        return int(float(value) * 1024 * 1024)
    except ValueError:
        return None                 # reported by check_settings


# the log is the history of every decision; past this size its oldest entries are deleted
LOG_MAX_BYTES = _megabytes(os.environ.get("SEMSYNC_LOG_MAX_MB", "10"))
DB = f"{BASE}/state.db"
POLL = 30
OUTPUT = os.environ.get("SEMSYNC_OUTPUT", "replace")        # "replace" or "side"
# replace mode: the download kept beside the correction is "visible" (an extra track) or "hidden"
KEEP_DOWNLOAD = os.environ.get("SEMSYNC_KEEP_DOWNLOAD", "visible")
# lines the video has no room for (a credit, a recap or a scene it lacks): "drop" or "keep"
EXTRA_LINES = os.environ.get("SEMSYNC_EXTRA_LINES", core.P["extra_lines"])
# queued jobs whose video or subtitle lies outside these folders (os.pathsep-separated) are
# skipped; empty: no restriction
MEDIA_ROOT = os.environ.get("SEMSYNC_MEDIA_ROOT", "")
UNTOUCHED, RESYNC = "untouched", "resync"
REPLACED = "replaced"               # the kept download's tag up to 0.12: '<video>.replaced.<lang...>.srt'
OWN_TAGS = (UNTOUCHED, REPLACED, RESYNC)
HIDDEN = ".orig"                    # SEMSYNC_KEEP_DOWNLOAD=hidden: '<subtitle>.orig', not a subtitle extension
# what follows a subtitle's title in its name: language codes and Jellyfin's flags
FLAG_TAGS = {"forced", "foreign", "default"} | media.HI_TAGS
CHANGED = "changed_during_run"      # the subtitle was replaced while it was being processed


def check_settings():
    """The settings read from the environment, checked before any job: a typo stops the worker
    with a clear message instead of acting as another value or failing on every job."""
    errors = []
    if OUTPUT not in ("replace", "side"):
        errors.append(f"SEMSYNC_OUTPUT={OUTPUT!r}: choose replace or side")
    if KEEP_DOWNLOAD not in ("visible", "hidden"):
        errors.append(f"SEMSYNC_KEEP_DOWNLOAD={KEEP_DOWNLOAD!r}: choose visible or hidden")
    if EXTRA_LINES not in ("drop", "keep"):
        errors.append(f"SEMSYNC_EXTRA_LINES={EXTRA_LINES!r}: choose drop or keep")
    if core.DEFAULT_MODEL not in core.MODELS:
        errors.append(f"SEMSYNC_MODEL={core.DEFAULT_MODEL!r}: choose one of {', '.join(core.MODELS)}")
    if LOG_MAX_BYTES is None or LOG_MAX_BYTES < 0:
        errors.append(f"SEMSYNC_LOG_MAX_MB={os.environ.get('SEMSYNC_LOG_MAX_MB')!r}: a size in MB, 0 for no limit")
    for root in filter(None, MEDIA_ROOT.split(os.pathsep)):
        if not os.path.isdir(root):
            errors.append(f"SEMSYNC_MEDIA_ROOT: {root!r} is not a folder")
    if errors:
        sys.exit("semantic-subsync-worker: " + "; ".join(errors))


# --- log ---------------------------------------------------------------------------------------

def log(rec, keep=True, detail=None):
    """One JSON line on stdout per decision (the Jellyfin plugin reads the last one). When `keep`,
    it is also added to the log file, with `detail` (e.g. a traceback) that stdout does not repeat."""
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    if keep:
        with _log_lock():
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({**rec, **(detail or {})}, ensure_ascii=False) + "\n")
            _trim(LOG_MAX_BYTES)
    return rec


@contextlib.contextmanager
def _log_lock():
    """The queue worker, `one` and `backfill` may write the log at the same time: a line must not
    be appended to a copy being trimmed. Without fcntl (Windows) there is no lock."""
    if fcntl is None:
        yield
        return
    with open(LOG + ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def trim_log(max_bytes=None):
    """Past SEMSYNC_LOG_MAX_MB, delete the oldest entries: the newest half of the limit is kept,
    cut at a line boundary. No archive copy is made. 0 = no limit."""
    with _log_lock():
        _trim(LOG_MAX_BYTES if max_bytes is None else max_bytes)


def _trim(max_bytes):
    if not max_bytes or os.path.getsize(LOG) <= max_bytes:
        return
    with open(LOG, "rb") as f:
        f.seek(-(max_bytes // 2), os.SEEK_END)
        f.readline()                                   # the first line read is a partial one
        kept = f.read()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LOG) or ".", prefix=".semsync.log.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(kept)
        os.replace(tmp, LOG)
    finally:
        _remove(tmp)


# --- names, as Jellyfin reads them -------------------------------------------------------------

def _is_tag(word):
    word = word.lower()
    return word in FLAG_TAGS or word in media.LANGUAGE_KEY or word in media.CODEPAGE


def _title_end(words):
    """Index of the first of the trailing language / flag words of a name split on dots."""
    i = len(words)
    while i > 1 and _is_tag(words[i - 1]):
        i -= 1
    return i


def tagged_path(video, sub, tag):
    """'Show - S01E01.fr.hi.srt' -> 'Show - S01E01.<tag>.fr.hi.srt'. Jellyfin reads what follows
    the video's name: the language and hi/forced flags, the other words being the track title.
    A subtitle not named after its video gets the tag before its language and flags as well."""
    stem = os.path.splitext(video)[0]
    if sub.startswith(stem + "."):
        return stem + f".{tag}" + sub[len(stem):]
    name = os.path.basename(sub)
    words = os.path.splitext(name)[0].split(".")
    i = _title_end(words)
    return sub[:len(sub) - len(name)] + ".".join(words[:i] + [tag] + words[i:]) + ".srt"


def kept_path(sub, keep=None):
    """Where replace mode keeps the download. Visible: 'Show.fr.hi.srt' -> 'Show.fr.hi.untouched.srt',
    the same language and flags for Jellyfin, "untouched" as the track's title. Jellyfin lists the
    external subtitles in file name order (culture-aware: '~' and '-' sort before letters) and, for
    a language, plays the first one: the tag goes last, after every word of the subtitle's name,
    and starts with a letter after the 's' of 'srt', so the correction comes first whatever the
    language. Hidden: 'Show.fr.hi.srt.orig', which neither Jellyfin nor Bazarr reads."""
    if (keep or KEEP_DOWNLOAD) == "hidden":
        return sub + HIDDEN
    root, ext = os.path.splitext(sub)
    return f"{root}.{UNTOUCHED}{ext}"


def former_kept_paths(video, sub):
    """Where an earlier version, or the other SEMSYNC_KEEP_DOWNLOAD value, kept the download."""
    other = "visible" if KEEP_DOWNLOAD == "hidden" else "hidden"
    return [kept_path(sub, other), tagged_path(video, sub, REPLACED)]


def side_path(video, sub):
    """'Show - S01E01.fr.srt' -> 'Show - S01E01.resync.fr.default.srt': the "default" flag makes Jellyfin
    pick the correction over the download, which keeps its name. A forced subtitle gets no flag: it
    would be chosen over the full subtitles."""
    path = tagged_path(video, sub, RESYNC)
    toks = os.path.basename(path).lower().split(".")[1:-1]
    if "forced" in toks or "default" in toks:
        return path
    return os.path.splitext(path)[0] + ".default.srt"


def is_own(path, video=None):
    """A file this worker wrote (or kept aside): never a job of its own. Only the words after the
    video's name count ('The.Replaced.2024.fr.srt' is a download); without the video, the last
    word, or the word before the language and flags (names written by earlier versions)."""
    name = os.path.basename(path).lower()
    if name.endswith(HIDDEN):
        return True
    if video:
        stem = os.path.basename(os.path.splitext(video)[0]).lower()
        if name.startswith(stem + "."):
            return any(t in OWN_TAGS for t in name[len(stem):].split(".")[1:-1])
    words = name.split(".")[:-1]
    i = _title_end(words)
    return len(words) > 1 and (words[-1] in OWN_TAGS or words[i - 1] in (REPLACED, RESYNC))


def settings(model):
    return {"extra_lines": EXTRA_LINES, "min_coverage": core.MIN_COVERAGE, "model": model,
            "min_sim": core.MODELS[model]["min_sim"]}


# --- files -------------------------------------------------------------------------------------

def _umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _stage(path, cues=None, copy_of=None, like=None):
    """Write what `path` will hold into a new temporary file beside it (same file system: moving
    it in place is atomic), flushed to disk, with the permissions of the file it replaces, else
    of `like`, else the usual ones (umask). Returns the temporary path."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix="." + os.path.basename(path) + ".",
                               suffix=".tmp")
    os.close(fd)
    try:
        if copy_of:
            shutil.copyfile(copy_of, tmp)
        else:
            core.write(tmp, cues)
        with open(tmp, "r+b") as f:
            os.fsync(f.fileno())
        mode = next((os.stat(p).st_mode & 0o777 for p in (path, like) if p and os.path.exists(p)),
                    0o666 & ~_umask())
        os.chmod(tmp, mode)
    except BaseException:
        _remove(tmp)
        raise
    return tmp


def _write_atomic(path, cues=None, copy_of=None, like=None):
    tmp = _stage(path, cues, copy_of, like)
    try:
        os.replace(tmp, path)
    finally:
        _remove(tmp)


def _remove(path):
    if path and os.path.exists(path):
        os.remove(path); return path
    return None


def _set_aside(path):
    """Keep a file under a name no player nor this worker reads: '<name>.<time>.bak'."""
    dest = f"{path}.{time.strftime('%Y%m%d-%H%M%S')}.{time.time_ns() % 10**9:09d}.bak"
    os.replace(path, dest)
    return dest


def inside_media_root(*paths):
    roots = [os.path.realpath(r) for r in MEDIA_ROOT.split(os.pathsep) if r]
    if not roots:
        return True
    def inside(p, root):
        try:
            return os.path.commonpath([root, os.path.realpath(p)]) == root
        except ValueError:          # another drive (Windows)
            return False
    return all(any(inside(p, r) for r in roots) for p in paths)


# --- one subtitle ------------------------------------------------------------------------------

def process(video, sub, origin="manual", force=False, score=None, state=None, model=None):
    """`model`: a model chosen by hand for this subtitle (kept for later runs), "default" to
    drop such a choice, None to keep what state.db says. Returns the logged record."""
    if model not in (None, "default"):
        core.check_model(model)
    t0 = time.time()
    rec = {"origin": origin, "video": video, "sub": sub}
    if not (os.path.isfile(video) and os.path.isfile(sub)) or not sub.lower().endswith(".srt") or is_own(sub, video):
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


def _records(row):
    """The recorded decision and, after an interrupted run, the one that was being written."""
    return [r for r in (row, (row or {}).get("pending")) if r]


def locate(sub, kept, row, cur):
    """The file that holds the downloaded subtitle. In replace mode `sub` may hold the worker's own
    correction (a hash recorded for it), the download being kept aside (`kept`); a new download
    overwrites `sub` (another hash). With no record at all (state.db lost or reset), an existing
    kept file can only be a download kept by an earlier run."""
    if not os.path.isfile(kept):
        return sub
    if row is None:
        return kept
    ours = {r.get("output_sha256") for r in _records(row) if r.get("output_path") == sub}
    return kept if cur in ours else sub


def move_kept(kept, former):
    """A download kept under a former name (by an earlier version, or under the other
    SEMSYNC_KEEP_DOWNLOAD value) takes the current name: a rename, nothing is overwritten. It is
    then treated as any file under the current name: the download, or a leftover that the run
    removes (known content) or sets aside. Returns [old, new], or None."""
    for old in former:
        if os.path.isfile(old) and not os.path.exists(kept):
            os.replace(old, kept)
            return [old, kept]
    return None


def _process(video, sub, rec, t0, force, score, state, model):
    kept, side, former = kept_path(sub), side_path(video, sub), former_kept_paths(video, sub)
    row = state.get(sub)
    choice = (row or {}).get("model_choice") if model is None else (None if model == "default" else model)
    use = choice or core.DEFAULT_MODEL
    cur = sha256(sub)
    renamed = move_kept(kept, former)
    if renamed:
        rec = {**rec, "renamed": renamed}
    source = locate(sub, kept, row, cur)
    src_sha = cur if source == sub else sha256(source)
    vsize, vmtime = video_stamp(video)
    # files left by an interrupted run, by another output mode, or under a former name beside the current one
    leftover = [p for p, wanted in ((side, row and row["output_path"] == side),
                                    (kept, row and row["output_path"] == sub)) if not wanted and os.path.exists(p)]
    leftover += [p for p in former if os.path.isfile(p)]
    if (not force and row and row["pending"] is None and row["input_sha256"] == src_sha
            and (row["video_size"], row["video_mtime"]) == (vsize, vmtime) and row["output_mode"] == OUTPUT
            and (model is None or (row["model"], row["model_choice"]) == (use, choice))
            and cur in (row["input_sha256"], row["output_sha256"])
            and kept_output(row, sub, side) and not leftover):
        if row["output_path"] == sub and row["replaced_path"] != kept:
            state.update(sub, replaced_path=kept)          # the download is now kept under this name
        # not kept in the log file unless a file was renamed: re-runs and backfills would fill it
        # and push real history out
        return log({**rec, "status": "unchanged", "last": row["status"], "processed_at": row["processed_at"]},
                   keep=bool(renamed))

    lang, kind = media.language_key(media.language_of(sub)), media.kind_of(sub)
    entry = {**rec, "lang": lang, "kind": kind, "input_sha256": src_sha, "input_size": os.path.getsize(source),
             "video_size": vsize, "video_mtime": vmtime, "output_mode": OUTPUT, "engine_version": __version__,
             "model": use, "model_choice": choice, "settings": settings(use), "score": score}
    # hashes of content that may be overwritten or removed: the download and the corrections, recorded
    # or being written. Any other content found where a file is about to go is set aside.
    known = {src_sha} | {r.get(k) for r in _records(row) for k in ("input_sha256", "output_sha256")}

    def settle(status, out=None, **info):
        """Put the files in their final state; record and log the decision. The intended state is
        recorded first, so a run interrupted at any step is recognised and completed next time."""
        outp = None if out is None else sub if OUTPUT == "replace" else side
        tmp = _stage(outp, out, like=sub) if outp else None
        cleaned, aside = [], []
        try:
            out_sha = sha256(tmp) if tmp else None
            # the downloader may have written a new subtitle during the run: it is a new job, this
            # result is not written
            if sha256(sub) != cur or (source != sub and (not os.path.isfile(source) or sha256(source) != src_sha)):
                return log({**rec, "status": CHANGED, "reason": "the subtitle changed while it was processed"})
            safe = known | {out_sha}

            def make_way(path, remove=False):
                """`path` is about to be overwritten (or removed): a content not accounted for is
                renamed instead."""
                if not os.path.isfile(path):
                    return
                if sha256(path) not in safe:
                    aside.append(_set_aside(path))
                elif remove:
                    cleaned.append(_remove(path))

            state.begin(sub, {"input_sha256": src_sha, "output_sha256": out_sha, "output_path": outp})
            for old in former:
                make_way(old, remove=True)         # a second copy under a former name
            if out is None or OUTPUT == "side":
                if source == kept:
                    make_way(sub)
                    os.replace(kept, sub)          # the download gets its name back
                else:
                    make_way(kept, remove=True)
            if out is None:
                cleaned.append(_remove(side))
            elif OUTPUT == "replace":
                if source == sub:
                    make_way(kept)
                    _write_atomic(kept, copy_of=sub, like=sub)   # the download stays, "untouched" or hidden
                make_way(sub)
                os.replace(tmp, sub)
                cleaned.append(_remove(side))
            else:
                os.replace(tmp, side)
            full = {**entry, "status": status, "secs": round(time.time() - t0, 1), **info,
                    "output_path": outp, "output_sha256": out_sha, "replaced_path": kept if outp == sub else None}
            state.put(full)
        finally:
            _remove(tmp)
        cleaned = [p for p in cleaned if p]
        return log({k: v for k, v in full.items() if v is not None} | ({"removed": cleaned} if cleaned else {})
                   | ({"kept_aside": aside} if aside else {}))

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
    return settle("corrected", out, **info)


def segments(st):
    """The correction, segment by segment: from / to (s, in the subtitle), offset at the start of
    the segment (s) and frame-rate drift (ppm; 23.976 -> 25 fps is about -40000)."""
    return [{"from": round(s["t0"], 2), "to": round(s["t1"], 2),
             "offset": round(s["a"] + s["drift_ppm"] * 1e-6 * s["t0"], 2), "drift_ppm": round(s["drift_ppm"])}
            for s in st.get("seg", [])] or None


# --- the queue ---------------------------------------------------------------------------------

def unload_model():
    """A model takes 0.2 to 0.5 GB: free it while the queue is empty. (The embedding cache is
    bounded by core, so a long backlog does not pile up vectors.)"""
    core.unload()


def queued_jobs():
    """Queued jobs, oldest first; a job removed meanwhile (by hand, by another worker) is left out."""
    jobs = []
    for name in os.listdir(QUEUE):
        if name.endswith(".job"):
            path = os.path.join(QUEUE, name)
            try:
                jobs.append((os.path.getmtime(path), name, path))
            except OSError:
                pass
    return [p for _, _, p in sorted(jobs)]


def run_job(job, state):
    """Process one job file and remove it; a failed job is moved to FAILED. Returns the status, or
    None when the job could not be removed nor moved (the caller must not take it again)."""
    try:
        with open(job, encoding="utf-8") as f:
            j = json.load(f)
        if not inside_media_root(j["video"], j["sub"]):
            status = log({"origin": j.get("origin", "bazarr"), "video": j["video"], "sub": j["sub"],
                          "status": "skipped", "reason": "outside SEMSYNC_MEDIA_ROOT"})["status"]
        else:
            status = process(j["video"], j["sub"], j.get("origin", "bazarr"), j.get("force", False), j.get("score"),
                             state, j.get("model"))["status"]
        dest = None
    except FileNotFoundError:
        if not os.path.exists(job):
            return "gone"                     # removed meanwhile
        status, dest = "error", os.path.join(FAILED, os.path.basename(job))
        _log_error(job)
    except Exception:
        status, dest = "error", os.path.join(FAILED, os.path.basename(job))
        _log_error(job)
    try:
        if dest:
            os.makedirs(FAILED, exist_ok=True)
            os.replace(job, dest)
        else:
            os.remove(job)
    except FileNotFoundError:
        pass
    except OSError as e:
        _log_error(job, f"cannot remove the job: {e!r}")
        return None
    return status


def _log_error(job, error=None):
    """The end of the traceback goes to the log file only; stdout gets one line."""
    exc = sys.exc_info()[1]
    try:
        log({"status": "error", "job": os.path.basename(job), "error": error or repr(exc)},
            detail={"trace": traceback.format_exc()[-2000:]} if exc else None)
    except Exception as e:                    # the log itself failed (disk full...): stderr
        print(f"semantic-subsync-worker: {job}: {error or repr(exc)} (log: {e!r})", file=sys.stderr, flush=True)


def drain(state, stuck=None):
    """Process the jobs queued now. `stuck`: jobs that could not be removed, not taken again.
    Returns {status: count}."""
    stuck = set() if stuck is None else stuck
    counts = {}
    for job in queued_jobs():
        if job in stuck:
            continue
        status = run_job(job, state)
        if status is None:
            stuck.add(job)
        elif status != "gone":
            counts[status] = counts.get(status, 0) + 1
    return counts


def run():
    os.makedirs(QUEUE, exist_ok=True); os.makedirs(FAILED, exist_ok=True)
    log({"status": "worker_started", "version": __version__, "output": OUTPUT, "keep_download": KEEP_DOWNLOAD,
         "extra_lines": EXTRA_LINES,
         "model": core.DEFAULT_MODEL, "model_dir": os.environ.get("SEMSYNC_MODEL_DIR"),
         "media_root": MEDIA_ROOT or None})
    state, stuck, done = State(DB), set(), {}
    while True:
        counts = drain(state, stuck)
        for k, v in counts.items():
            done[k] = done.get(k, 0) + v
        if not counts:
            if done:                          # one line for the batch: `unchanged` jobs are not logged one by one
                log({"status": "queue_empty", "jobs": sum(done.values()), "counts": done}); done = {}
            unload_model(); time.sleep(POLL)


def enqueue(job, n=0):
    """Drop a job file into the queue: written under a temporary name then renamed, so the worker
    never reads half a job; the name is unique (time, then `n`), so no pending job is overwritten."""
    os.makedirs(QUEUE, exist_ok=True)
    name = os.path.join(QUEUE, f"{time.time_ns()}-{n:06d}-{job.get('origin', 'job')}.job")
    with open(name + ".tmp", "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
    os.replace(name + ".tmp", name)
    return name


def backfill(root):
    """Queue every external .srt that sits next to its video (the longest video name it starts
    with: 'Movie.Extended.fr.srt' belongs to 'Movie.Extended.mkv', not 'Movie.mkv'). Subtitles
    already processed and unchanged are skipped quickly by the worker (state.db)."""
    n = 0
    for d, _, files in os.walk(root):
        videos = sorted((f for f in files if f.lower().endswith(media.VIDEO_EXT)),
                        key=lambda v: len(os.path.splitext(v)[0]), reverse=True)
        for f in sorted(files):
            if not f.lower().endswith(".srt"):
                continue
            v = next((x for x in videos if f.startswith(os.path.splitext(x)[0] + ".")), None)
            if v and not is_own(f, v):
                enqueue({"video": os.path.join(d, v), "sub": os.path.join(d, f), "origin": "backfill"}, n)
                n += 1
    log({"status": "backfill_queued", "root": root, "jobs": n})


# --- reading the state and the log -------------------------------------------------------------

def matches(path, terms):
    """True when the path contains every term, whatever the case: `TITLE S01E02` does not
    match the S01E02 of another series."""
    path = (path or "").lower()
    return all(t.lower() in path for t in terms)


def status(terms, fields=False):
    if fields:
        for k, v in FIELDS.items():
            print(f"{k:16} {v}")
        return
    rows = [r for r in State(DB).all() if matches(r["sub"], terms)]
    counts = {}
    for r in rows:
        st = r["status"] or "pending"            # a first run interrupted while writing its files
        counts[st] = counts.get(st, 0) + 1
        print(f"{r['processed_at'] or '':19}  {st:12} {r['sub']}")
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
            for p in r.get("kept_aside") or []:
                print(f"      kept aside as {os.path.basename(p)}")
        print()
    if len(groups) > 1:
        print(f"{len(groups)} subtitles match: add words to narrow down (series, season, language)")


def parser():
    p = argparse.ArgumentParser(prog="semantic-subsync-worker", formatter_class=argparse.RawDescriptionHelpFormatter,
                                description=__doc__)
    cmds = p.add_subparsers(dest="cmd", metavar="COMMAND")
    cmds.add_parser("run", help="process the queue forever (the default)")
    one = cmds.add_parser("one", help="process one pair now")
    one.add_argument("video")
    one.add_argument("sub")
    one.add_argument("--force", action="store_true", help="process it again even if nothing changed")
    one.add_argument("--model", choices=[*core.MODELS, "default"], help="model for this subtitle, kept for later runs")
    b = cmds.add_parser("backfill", help="enqueue every external .srt next to its video")
    b.add_argument("root", nargs="?", default="/data/media")
    s = cmds.add_parser("status", help="what state.db knows, for the paths containing every WORD")
    s.add_argument("words", nargs="*", metavar="WORD")
    s.add_argument("--fields", action="store_true", help="what each column means")
    h = cmds.add_parser("history", help="every logged decision about the paths containing every WORD")
    h.add_argument("words", nargs="+", metavar="WORD")
    cmds.add_parser("prepare", help="download and load the sentence model now")
    return p


def main(argv=None):
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    cmd = args.cmd or "run"
    if cmd in ("run", "one", "backfill", "prepare"):
        check_settings()
    if hasattr(os, "nice"):         # POSIX only
        os.nice(19)                 # Jellyfin keeps priority on the CPU
    if cmd == "run":
        run()
    elif cmd == "one":
        os.makedirs(BASE, exist_ok=True)
        process(args.video, args.sub, force=args.force, model=args.model)
    elif cmd == "backfill":
        backfill(args.root)
    elif cmd == "status":
        status(args.words, args.fields)
    elif cmd == "history":
        history(args.words)
    elif cmd == "prepare":
        t0 = time.time(); core.embed(["ready"], model=core.DEFAULT_MODEL)
        print(json.dumps({"status": "ready", "model": core.DEFAULT_MODEL, "secs": round(time.time() - t0, 1)}))


if __name__ == "__main__":
    main()
