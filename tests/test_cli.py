"""Command line: argument handling, decisions, exit status, output file."""
import json, subprocess
from pathlib import Path

import pytest

from semantic_subsync import cli, core, media
from synth import dialogue, ref_cues, tgt_cues, use_fake_model


@pytest.fixture
def files(tmp_path, monkeypatch):
    use_fake_model(monkeypatch)
    base = dialogue(n=200, seed=33)
    ref = tmp_path / "Movie.en.srt"
    core.write(str(ref), ref_cues(base))
    return dict(tmp=tmp_path, base=base, ref=str(ref), sub=str(tmp_path / "Movie.fr.srt"))


def test_model_option_reaches_the_engine(files, monkeypatch, capsys):
    core.write(files["sub"], tgt_cues(files["base"], warp=lambda t: t + 10))
    used = []
    real = core.embed
    monkeypatch.setattr(core, "embed", lambda texts, model=None: used.append(model) or real(texts, model))
    assert cli.main([files["sub"], files["ref"], "--model", "minilm", "--json"]) == cli.EXIT_OK
    assert set(used) == {"minilm"} and json.loads(capsys.readouterr().out)["model"] == "minilm"
    with pytest.raises(SystemExit):
        cli.main([files["sub"], files["ref"], "--model", "bert"])


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


# ---------- errors: one line on stderr, exit status 2 ----------

def _one_line_error(capsys, *words):
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "Traceback" not in err and all(w in err for w in words), err


def test_crash_is_an_error_not_unsure(files, monkeypatch, capsys):
    core.write(files["sub"], tgt_cues(files["base"]))
    def no_tokenizers(*a, **k):
        raise ImportError("the static model needs tokenizers")
    monkeypatch.setattr(core, "resync", no_tokenizers)
    assert cli.main([files["sub"], files["ref"]]) == cli.EXIT_ERROR
    _one_line_error(capsys, "tokenizers")


def test_ffprobe_failure_is_an_error(files, monkeypatch, capsys):
    core.write(files["sub"], tgt_cues(files["base"]))
    video = files["tmp"] / "Movie.mkv"
    video.write_bytes(b"")
    def broken(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "[mov] moov atom not found\nInvalid data found\n")
    monkeypatch.setattr(media.subprocess, "run", broken)
    assert cli.main([files["sub"], str(video)]) == cli.EXIT_ERROR
    _one_line_error(capsys, "ffprobe", "Invalid data found")


def test_bad_model_from_environment(files, monkeypatch, capsys):
    core.write(files["sub"], tgt_cues(files["base"]))
    monkeypatch.setattr(core, "DEFAULT_MODEL", "bert")         # $SEMSYNC_MODEL=bert
    assert cli.main([files["sub"], files["ref"]]) == cli.EXIT_ERROR
    _one_line_error(capsys, "bert", "SEMSYNC_MODEL")


@pytest.mark.parametrize("case", ["ass_reference", "vtt_subtitle", "empty_subtitle", "track_with_srt", "output_is_reference"])
def test_refused_inputs(files, case, capsys):
    core.write(files["sub"], tgt_cues(files["base"]))
    sub, ref, extra = files["sub"], files["ref"], []
    if case == "ass_reference":
        ref = str(files["tmp"] / "Movie.en.ass"); Path(ref).write_text("[Script Info]\n", encoding="utf-8")
    elif case == "vtt_subtitle":
        sub = str(files["tmp"] / "Movie.fr.vtt"); Path(sub).write_text("WEBVTT\n", encoding="utf-8")
    elif case == "empty_subtitle":
        Path(sub).write_text("not a subtitle\n", encoding="utf-8")
    elif case == "track_with_srt":
        extra = ["--track", "2"]
    else:
        extra = ["-o", ref]
    before = Path(files["ref"]).read_bytes()
    assert cli.main([sub, ref, *extra]) == cli.EXIT_ERROR
    _one_line_error(capsys)
    assert Path(files["ref"]).read_bytes() == before


def test_explicit_short_track_is_explained(files, monkeypatch, capsys):
    core.write(files["sub"], tgt_cues(files["base"]))
    video = files["tmp"] / "Movie.mkv"
    video.write_bytes(b"")
    monkeypatch.setattr(media, "embedded_references",
                        lambda v, wd, streams=None, tracks=None: [(ref_cues(files["base"][:12]), "#5 eng Forced")])
    assert cli.main([files["sub"], str(video), "--track", "5"]) == cli.EXIT_ERROR
    _one_line_error(capsys, "#5", "12 lines")


def test_too_few_cues_message_has_no_coverage(files, capsys):
    core.write(files["sub"], tgt_cues(files["base"][:10]))
    assert cli.main([files["sub"], files["ref"]]) == cli.EXIT_UNSURE
    err = capsys.readouterr().err
    assert "too_few_cues" in err and "coverage" not in err


def test_lang_is_case_insensitive():
    assert cli.parser().parse_args(["a.srt", "b.srt", "--lang", "RU"]).lang == "ru"


# ---------- media: ffprobe / ffmpeg ----------

def test_missing_ffmpeg_is_a_clear_error(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(media.subprocess, "run", missing)
    with pytest.raises(media.MediaError, match="ffprobe not found"):
        media.probe_subtitles("v.mkv")


def test_ffmpeg_failure_is_not_an_empty_result(tmp_path, monkeypatch):
    monkeypatch.setattr(media.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "v.mkv: End of file\n"))
    tracks = [{"index": 2, "codec": "subrip", "text": True, "lang": "en", "kind": "normal", "title": ""}]
    with pytest.raises(media.MediaError, match="End of file"):
        media.embedded_references("v.mkv", str(tmp_path), tracks=tracks)
