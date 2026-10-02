"""Command line: argument handling, decisions, exit status, output file."""
import json
from pathlib import Path

import pytest

from semantic_subsync import cli, core, media
from synth import dialogue, fake_embed, ref_cues, tgt_cues


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "embed", fake_embed)
    base = dialogue(n=200, seed=33)
    ref = tmp_path / "Movie.en.srt"
    core.write(str(ref), ref_cues(base))
    return dict(tmp=tmp_path, base=base, ref=str(ref), sub=str(tmp_path / "Movie.fr.srt"))


def test_default_output():
    assert cli.default_output("/m/Movie.fr.srt") == "/m/Movie.fr.synced.srt"


def test_corrected_with_srt_reference(files, capsys):
    core.write(files["sub"], tgt_cues(files["base"], warp=lambda t: t + 10))
    before = Path(files["sub"]).read_bytes()
    assert cli.main([files["sub"], files["ref"], "--json"]) == cli.EXIT_OK
    rec = json.loads(capsys.readouterr().out)
    assert rec["status"] == "corrected" and rec["output"] == cli.default_output(files["sub"])
    out = core.parse(rec["output"])
    assert abs(out[0][0] - files["base"][0][0]) < 0.05
    assert Path(files["sub"]).read_bytes() == before


def test_in_sync_writes_nothing(files, capsys):
    core.write(files["sub"], tgt_cues(files["base"]))
    assert cli.main([files["sub"], files["ref"]]) == cli.EXIT_OK
    assert "already in sync" in capsys.readouterr().err
    assert not Path(cli.default_output(files["sub"])).exists()


def test_unsure_exit_status(files):
    core.write(files["sub"], tgt_cues(files["base"], warp=lambda t: t + 10))
    core.write(files["ref"], [[s, e, f"line k{k + 9999}"] for s, e, k in files["base"]])   # unrelated
    assert cli.main([files["sub"], files["ref"], "-o", str(files["tmp"] / "out.srt")]) == cli.EXIT_UNSURE
    assert not (files["tmp"] / "out.srt").exists()


def test_video_reference_uses_fullest_embedded_track(files, monkeypatch, capsys):
    core.write(files["sub"], tgt_cues(files["base"], warp=lambda t: t + 10))
    video = files["tmp"] / "Movie.mkv"
    video.write_bytes(b"")
    seen = {}

    def fake_refs(v, wd, streams=None, tracks=None):
        seen["streams"] = streams
        return [(ref_cues(files["base"][:25]), "#4 eng Signs"), (ref_cues(files["base"]), "#3 eng English")]
    monkeypatch.setattr(media, "embedded_references", fake_refs)
    assert cli.main([files["sub"], str(video), "--json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["reference"] == "#3 eng English"
    assert seen["streams"] is None
    cli.main([files["sub"], str(video), "--track", "3"])
    assert seen["streams"] == {3}


def test_video_without_text_subtitle(files, monkeypatch, capsys):
    core.write(files["sub"], tgt_cues(files["base"]))
    video = files["tmp"] / "Movie.mkv"
    video.write_bytes(b"")
    monkeypatch.setattr(media, "embedded_references", lambda v, wd, streams=None, tracks=None: [])
    assert cli.main([files["sub"], str(video)]) == cli.EXIT_ERROR
    assert "no usable embedded text subtitle" in capsys.readouterr().err


@pytest.mark.parametrize("which", ["missing", "overwrite"])
def test_errors(files, which):
    core.write(files["sub"], tgt_cues(files["base"]))
    args = [files["sub"] + ".gone", files["ref"]] if which == "missing" else [files["sub"], files["ref"], "-o", files["sub"]]
    assert cli.main(args) == cli.EXIT_ERROR
