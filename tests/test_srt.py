"""SRT parsing / writing and text cleaning."""
import pytest

from semantic_subsync import core


def test_roundtrip(tmp_path):
    cues = [[1.0, 2.5, "Hello"], [3.25, 4.0, "Two\nlines"], [3725.123, 3726.0, "Café after an hour"]]
    p = tmp_path / "a.srt"
    core.write(p, cues)
    assert core.parse(p) == cues


def test_parse_bom_crlf_dot_ms_and_noise(tmp_path):
    p = tmp_path / "a.srt"
    p.write_bytes("﻿1\r\n00:00:01.500 --> 00:00:02,000\r\nOne\r\n\r\n"
                  "garbage block\r\n\r\n"
                  "2\r\n00:00:00,100 --> 00:00:00,900\r\nBefore\r\n".encode("utf-8"))
    assert core.parse(p) == [[0.1, 0.9, "Before"], [1.5, 2.0, "One"]]   # sorted, junk ignored


@pytest.mark.parametrize("eol", ["\r\r\n", "\r", "\n", "\r\n"])
def test_parse_any_line_ending(tmp_path, eol):
    """'\\r\\r\\n' comes from subtitles converted twice; a text-mode read would see blank lines
    and lose every cue's text."""
    p = tmp_path / "a.srt"
    p.write_bytes(eol.join(["1", "00:00:01,000 --> 00:00:02,000", "Line one", "line two", "",
                            "2", "00:00:03,000 --> 00:00:04,000", "B", ""]).encode("utf-8"))
    assert core.parse(p) == [[1.0, 2.0, "Line one\nline two"], [3.0, 4.0, "B"]]


def test_parse_missing_index_and_extra_blank_lines(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text("00:00:01,000 --> 00:00:02,000\nNo index\n\n\n\n3\n00:00:05,000 --> 00:00:06,000\nB\n",
                 encoding="utf-8")
    assert [c[2] for c in core.parse(p)] == ["No index", "B"]


def test_parse_empty_file(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text("", encoding="utf-8")
    assert core.parse(p) == []


@pytest.mark.parametrize("t,expected", [
    (0, "00:00:00,000"), (-1.2, "00:00:00,000"), (59.9996, "00:01:00,000"),
    (3600 + 61.5, "01:01:01,500"), (1.0005, "00:00:01,000"),
])
def test_fmt(t, expected):
    assert core.fmt(t) == expected


@pytest.mark.parametrize("raw,expected", [
    ("<i>Hello</i>", "Hello"),
    ("{\\an8}On top", "On top"),
    ("[MUSIC] Hi (sighs)", "Hi"),
    ("- Yes?\n- No.", "Yes? No."),
    ("JOHN: Hello there", "Hello there"),
    ("♪ la la la ♪", "la la la"),
    ("   multiple \n spaces  ", "multiple spaces"),
    ("[laughs]", ""),
])
def test_clean(raw, expected):
    assert core.clean(raw) == expected


@pytest.mark.parametrize("timing,start,end", [
    ("00:00:01,5 --> 00:00:02,25", 1.5, 2.25),           # fraction of 1 or 2 digits: tenths, hundredths
    ("00:00:01,5000 --> 00:00:02,0001", 1.5, 2.0001),
    ("01:02.500 --> 01:04.000", 62.5, 64.0),             # no hours
    ("1:00:00,000 --> 1:00:01,000", 3600.0, 3601.0),
])
def test_parse_timing_variants(timing, start, end):
    assert core.parse_text(f"1\n{timing}\nText\n") == [[start, end, "Text"]]


def test_parse_reads_any_encoding(tmp_path):
    p = tmp_path / "Movie.ru.srt"
    p.write_bytes("1\r\n00:00:01,000 --> 00:00:02,000\r\nПривет, как дела?\r\n".encode("cp1251"))
    assert core.parse(p)[0][2] == "Привет, как дела?"


def test_write_is_atomic_with_unix_line_ends(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text("old content", encoding="utf-8")
    core.write(p, [[1.0, 2.0, "A"]])
    assert p.read_bytes() == b"1\n00:00:01,000 --> 00:00:02,000\nA\n\n"
    assert [f.name for f in tmp_path.iterdir()] == ["a.srt"]
