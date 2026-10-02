"""What the worker knows about each downloaded subtitle it has seen (SQLite, one row per subtitle path).

The file name alone cannot tell whether a subtitle was already checked: with the default output
mode the corrected subtitle takes the name of the downloaded one, a downloader may overwrite it
with a new download, and the video next to it may be replaced. Content hashes and video sizes /
dates settle it: a subtitle is processed again only when one of them changed (or on demand).
"""
import hashlib, json, os, sqlite3, time

# column -> meaning (also the documentation printed by `semantic-subsync-worker status --fields`)
FIELDS = {
    "sub": "path of the downloaded subtitle (the key)",
    "video": "path of its video",
    "lang": "language from the file name (fr, en, pt-br...)",
    "kind": "normal, hi (hearing impaired) or forced, from the file name",
    "status": "corrected, in_sync, unsure, no_reference or redundant",
    "reason": "why it was unsure or redundant",
    "reference": "embedded track used as reference (#index language title)",
    "coverage": "share of lines anchored on the reference (unsure below the threshold)",
    "segments": "constant-offset segments found",
    "max_abs_offset": "largest shift applied, in seconds",
    "dropped": "lines left out (scenes or a recap the video lacks)",
    "seg": "the correction per segment, JSON: from / to (s), offset (s), drift_ppm",
    "input_sha256": "hash of the downloaded subtitle that was synced",
    "input_size": "its size in bytes",
    "output_mode": "replace or side",
    "output_path": "file holding the correction (the subtitle path itself in replace mode)",
    "output_sha256": "hash of the correction, to recognise our own file later",
    "replaced_path": "where the downloaded subtitle was kept (replace mode)",
    "video_size": "video size when processed (a replaced video triggers a new run)",
    "video_mtime": "video modification time when processed",
    "engine_version": "semantic-subsync version",
    "model": "sentence model used: static or minilm",
    "model_choice": "model chosen by hand (worker one --model), used again on later runs; empty: SEMSYNC_MODEL",
    "settings": "settings that change the result (extra lines, coverage threshold, model and its min_sim)",
    "origin": "who queued it: bazarr, backfill, manual",
    "score": "score given by the downloader, if any",
    "secs": "processing time in seconds",
    "processed_at": "date of the last run",
    "runs": "how many times it was processed",
}
_REAL = {"coverage", "max_abs_offset", "video_mtime", "secs"}
_INT = {"segments", "dropped", "input_size", "video_size", "runs"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def video_stamp(path):
    st = os.stat(path)
    return st.st_size, round(st.st_mtime, 3)


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        cols = ", ".join(f"{c} {'REAL' if c in _REAL else 'INTEGER' if c in _INT else 'TEXT'}"
                         + (" PRIMARY KEY" if c == "sub" else "") for c in FIELDS)
        self.db.execute(f"CREATE TABLE IF NOT EXISTS subtitles ({cols})")
        self.db.commit()

    def get(self, sub):
        r = self.db.execute("SELECT * FROM subtitles WHERE sub = ?", (sub,)).fetchone()
        return dict(r) if r else None

    def put(self, rec):
        old = self.get(rec["sub"]) or {}
        row = {c: rec.get(c) for c in FIELDS}
        row["runs"] = (old.get("runs") or 0) + 1
        row["processed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        for k in ("settings", "seg"):
            if isinstance(row[k], (dict, list)):
                row[k] = json.dumps(row[k], sort_keys=True)
        self.db.execute(f"INSERT OR REPLACE INTO subtitles ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                        list(row.values()))
        self.db.commit()

    def all(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM subtitles ORDER BY processed_at DESC")]

    def close(self):
        self.db.close()
