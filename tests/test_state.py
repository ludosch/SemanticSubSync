"""state.db written by older versions keeps working: missing columns are added, old status names
are mapped to the current ones."""
import sqlite3

from semantic_subsync.state import FIELDS, State

# the table as version 0.10 created it (no model, model_choice, seg nor pending column)
SCHEMA_0_10 = """CREATE TABLE subtitles (sub TEXT PRIMARY KEY, video TEXT, lang TEXT, kind TEXT, status TEXT,
reason TEXT, reference TEXT, coverage REAL, segments INTEGER, max_abs_offset REAL, dropped INTEGER,
input_sha256 TEXT, input_size INTEGER, output_mode TEXT, output_path TEXT, output_sha256 TEXT,
replaced_path TEXT, video_size INTEGER, video_mtime REAL, engine_version TEXT, settings TEXT, origin TEXT,
score TEXT, secs REAL, processed_at TEXT, runs INTEGER)"""


def old_db(path):
    db = sqlite3.connect(path)
    db.execute(SCHEMA_0_10)
    db.execute("INSERT INTO subtitles (sub, status, reason, input_sha256, runs, processed_at) VALUES "
               "('/m/A.fr.srt', 'refused', 'coverage', 'abc', 2, '2026-01-01 00:00:00'),"
               "('/m/B.fr.srt', 'corrected', NULL, 'def', 1, '2026-01-01 00:00:00')")
    db.commit()
    db.close()


def test_a_0_10_database_is_upgraded_in_place(tmp_path):
    path = str(tmp_path / "state.db")
    old_db(path)
    st = State(path)
    try:
        a = st.get("/m/A.fr.srt")
        assert set(a) == set(FIELDS)
        assert a["status"] == "unsure" and a["reason"] == "coverage" and a["model"] is None and a["pending"] is None
        assert st.get("/m/B.fr.srt")["status"] == "corrected"
        st.put({"sub": "/m/A.fr.srt", "status": "corrected", "model": "static", "seg": [{"from": 0}]})
        a = st.get("/m/A.fr.srt")
        assert a["status"] == "corrected" and a["model"] == "static" and a["runs"] == 3
    finally:
        st.close()
    State(path).close()                         # opening it again changes nothing


def test_begin_then_put(tmp_path):
    st = State(str(tmp_path / "state.db"))
    try:
        intent = {"input_sha256": "a", "output_sha256": "b", "output_path": "/m/A.fr.srt"}
        st.begin("/m/A.fr.srt", intent)
        r = st.get("/m/A.fr.srt")
        assert r["pending"] == intent and r["status"] is None and r["runs"] == 0
        st.put({"sub": "/m/A.fr.srt", "status": "corrected"})
        r = st.get("/m/A.fr.srt")
        assert r["pending"] is None and r["runs"] == 1
        st.begin("/m/A.fr.srt", intent)           # an existing row keeps its decision until put
        assert st.get("/m/A.fr.srt")["status"] == "corrected"
    finally:
        st.close()
