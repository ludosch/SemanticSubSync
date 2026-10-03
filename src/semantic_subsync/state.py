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
    "output_sha256": "hash of the correction, to recognise the worker's own file later",
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
    "pending": "files being written, JSON (input_sha256, output_sha256, output_path): recorded before they "
               "change and cleared once the decision is recorded, so an interrupted run is recognised",
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
        # a database written by an older version: its missing columns are added (empty), and the
        # decision 0.10 called "refused" is the one called "unsure" since
        have = {r[1] for r in self.db.execute("PRAGMA table_info(subtitles)")}
        for c in FIELDS:
            if c not in have:
                self.db.execute(f"ALTER TABLE subtitles ADD COLUMN {c} {'REAL' if c in _REAL else 'INTEGER' if c in _INT else 'TEXT'}")
        self.db.execute("UPDATE subtitles SET status = 'unsure' WHERE status = 'refused'")
        self.db.commit()

    def get(self, sub):
        r = self.db.execute("SELECT * FROM subtitles WHERE sub = ?", (sub,)).fetchone()
        if not r:
            return None
        r = dict(r)
        r["pending"] = json.loads(r["pending"]) if r["pending"] else None
        return r

    def begin(self, sub, intent):
        """Record the files about to be written for `sub` (`intent`: input_sha256, output_sha256,
        output_path) before any of them changes; `put` clears it. A run interrupted in between
        leaves both the previous and the intended hashes, so every file can still be recognised."""
        if not self.db.execute("UPDATE subtitles SET pending = ? WHERE sub = ?", (json.dumps(intent), sub)).rowcount:
            self.db.execute("INSERT INTO subtitles (sub, pending, runs) VALUES (?, ?, 0)", (sub, json.dumps(intent)))
        self.db.commit()

    def put(self, rec):
        old = self.get(rec["sub"]) or {}
        row = {c: rec.get(c) for c in FIELDS}
        row["pending"] = None
        row["runs"] = (old.get("runs") or 0) + 1
        row["processed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        for k in ("settings", "seg"):
            if isinstance(row[k], (dict, list)):
                row[k] = json.dumps(row[k], sort_keys=True)
        self.db.execute(f"INSERT OR REPLACE INTO subtitles ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                        list(row.values()))
        self.db.commit()

    def update(self, sub, /, **fields):
        """Change some columns of a recorded subtitle (e.g. `replaced_path` after a rename), leaving
        its decision, run count and date as they are. Returns whether the row exists."""
        refused = set(fields) - (set(FIELDS) - {"sub", "pending", "runs"})
        if refused or not fields:
            raise ValueError(f"cannot update {', '.join(sorted(refused)) or 'nothing'}")
        cur = self.db.execute(f"UPDATE subtitles SET {', '.join(f'{k} = ?' for k in fields)} WHERE sub = ?",
                              [*fields.values(), sub])
        self.db.commit()
        return cur.rowcount > 0

    def all(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM subtitles ORDER BY processed_at DESC")]

    def close(self):
        self.db.close()
