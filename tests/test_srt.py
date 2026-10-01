"""SRT parsing / writing and text cleaning."""
import pytest

from semantic_subsync import core


def test_roundtrip(tmp_path):
    cues = [[1.0, 2.5, "Bonjour"], [3.25, 4.0, "Deux\nlignes"], [3725.123, 3726.0, "Après une heure"]]
    p = tmp_path / "a.srt"
    core.write(p, cues)
    assert core.parse(p) == cues


def test_parse_bom_crlf_dot_ms_and_noise(tmp_path):
    p = tmp_path / "a.srt"
    p.write_bytes("﻿1\r\n00:00:01.500 --> 00:00:02,000\r\nUn\r\n\r\n"
                  "garbage block\r\n\r\n"
                  "2\r\n00:00:00,100 --> 00:00:00,900\r\nAvant\r\n".encode("utf-8"))
    assert core.parse(p) == [[0.1, 0.9, "Avant"], [1.5, 2.0, "Un"]]   # sorted, junk ignored


@pytest.mark.parametrize("eol", ["\r\r\n", "\r", "\n", "\r\n"])
def test_parse_any_line_ending(tmp_path, eol):
    """'\\r\\r\\n' comes from subtitles converted twice; a text-mode read would see blank lines
    and lose every cue's text."""
    p = tmp_path / "a.srt"
    p.write_bytes(eol.join(["1", "00:00:01,000 --> 00:00:02,000", "Ligne un", "ligne deux", "",
                            "2", "00:00:03,000 --> 00:00:04,000", "B", ""]).encode("utf-8"))
    assert core.parse(p) == [[1.0, 2.0, "Ligne un\nligne deux"], [3.0, 4.0, "B"]]


def test_parse_missing_index_and_extra_blank_lines(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text("00:00:01,000 --> 00:00:02,000\nSans numéro\n\n\n\n3\n00:00:05,000 --> 00:00:06,000\nB\n",
                 encoding="utf-8")
    assert [c[2] for c in core.parse(p)] == ["Sans numéro", "B"]


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
    ("<i>Bonjour</i>", "Bonjour"),
    ("{\\an8}En haut", "En haut"),
    ("[MUSIQUE] Salut (soupir)", "Salut"),
    ("- Oui ?\n- Non.", "Oui ? Non."),
    ("JOHN: Hello there", "Hello there"),
    ("♪ la la la ♪", "la la la"),
    ("   espaces \n multiples  ", "espaces multiples"),
    ("[rires]", ""),
])
def test_clean(raw, expected):
    assert core.clean(raw) == expected
