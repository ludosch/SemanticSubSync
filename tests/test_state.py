"""state.db written by older versions keeps working: missing columns are added, old status names
are mapped to the current ones."""
import sqlite3

import pytest

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


def test_update_changes_some_columns_only(tmp_path):
    """A kept download renamed (a new name, the other keep mode): its path is recorded without
    counting a run nor changing the decision."""
    st = State(str(tmp_path / "state.db"))
    try:
        st.put({"sub": "/m/A.ru.srt", "status": "corrected", "replaced_path": "/m/A.replaced.ru.srt"})
        before = st.get("/m/A.ru.srt")
        assert st.update("/m/A.ru.srt", replaced_path="/m/A.ru.untouched.srt")
        after = st.get("/m/A.ru.srt")
        assert after == {**before, "replaced_path": "/m/A.ru.untouched.srt"}
        assert not st.update("/m/B.ru.srt", replaced_path="x")              # no such row: nothing added
        assert st.get("/m/B.ru.srt") is None
        for bad in ({"runs": 5}, {"pending": "{}"}, {"sub": "/m/C.srt"}, {"nope": 1}, {}):
            with pytest.raises(ValueError):
                st.update("/m/A.ru.srt", **bad)
    finally:
        st.close()
