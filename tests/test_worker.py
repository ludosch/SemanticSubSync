"""Worker: file naming, encodings, decisions written next to the video, queue, Bazarr hook.
ffmpeg is not needed: the embedded-track extraction is replaced by synthetic references."""
import json, os, subprocess, sys
from pathlib import Path

import pytest

from semantic_subsync import core, worker
from synth import dialogue, fake_embed, ref_cues, tgt_cues

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A media folder with one video + downloaded subtitle; the model is the fake embedder."""
    monkeypatch.setattr(worker, "LOG", str(tmp_path / "semsync.log"))
    monkeypatch.setattr(worker, "QUEUE", str(tmp_path / "queue"))
    monkeypatch.setattr(worker, "FAILED", str(tmp_path / "failed"))
    monkeypatch.setattr(core, "embed", fake_embed)
    media = tmp_path / "Show" / "Season 1"
    media.mkdir(parents=True)
    video = media / "Show - S01E01.mkv"
    video.write_bytes(b"")
    sub = media / "Show - S01E01.fr.srt"
    base = dialogue(n=200, seed=21)
    refs = [(ref_cues(base), "#3 eng English")]
    monkeypatch.setattr(worker, "references", lambda v, wd: refs)
    return dict(tmp=tmp_path, video=str(video), sub=str(sub), base=base, refs=refs)


def last_log(env):
    return json.loads(Path(worker.LOG).read_text(encoding="utf-8").splitlines()[-1])


@pytest.mark.parametrize("video,sub,expected", [
    ("/m/Show - S01E01.mkv", "/m/Show - S01E01.fr.srt", "/m/Show - S01E01.semsync.fr.srt"),
    ("/m/Show - S01E01.mkv", "/m/Show - S01E01.fr.hi.srt", "/m/Show - S01E01.semsync.fr.hi.srt"),
    ("/m/Film (2001).mp4", "/m/Film (2001).fr.forced.srt", "/m/Film (2001).semsync.fr.forced.srt"),
    ("/m/A.mkv", "/m/other name.fr.srt", "/m/other name.fr.semsync.srt"),      # not the video's stem
])
def test_side_path(video, sub, expected):
    assert worker.side_path(video, sub) == expected


def test_read_srt_cp1252(tmp_path):
    p = tmp_path / "a.srt"
    # French on purpose: accented letters are where cp1252 and UTF-8 differ
    p.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nÇa été déjà là\n".encode("cp1252"))
    assert worker.read_srt(str(p))[0][2] == "Ça été déjà là"


def test_read_srt_utf8_bom(tmp_path):
    p = tmp_path / "a.srt"
    p.write_bytes("﻿1\n00:00:01,000 --> 00:00:02,000\nÇa\n".encode("utf-8"))
    assert worker.read_srt(str(p))[0][2] == "Ça"


def test_corrected_writes_side_file_and_keeps_original(env):
    core.write(env["sub"], tgt_cues(env["base"], warp=lambda t: t + 10))
    before = Path(env["sub"]).read_bytes()
    worker.process(env["video"], env["sub"])
    side = Path(worker.side_path(env["video"], env["sub"]))
    rec = last_log(env)
    assert rec["status"] == "corrected" and rec["reference"] == "#3 eng English"
    assert side.exists() and Path(env["sub"]).read_bytes() == before
    out = core.parse(side)
    assert abs(out[0][0] - env["base"][0][0]) < 0.05
    assert not list(side.parent.glob("*.tmp"))


def test_in_sync_writes_nothing_and_removes_stale(env):
    core.write(env["sub"], tgt_cues(env["base"]))
    side = Path(worker.side_path(env["video"], env["sub"]))
    side.write_text("stale", encoding="utf-8")
    worker.process(env["video"], env["sub"])
    rec = last_log(env)
    assert rec["status"] == "in_sync" and rec["removed_stale"] == str(side)
    assert not side.exists()


def test_refused_on_unrelated_reference(env):
    core.write(env["sub"], tgt_cues(env["base"], warp=lambda t: t + 10))
    env["refs"][:] = [([[s, e, f"line k{k + 9999}"] for s, e, k in env["base"]], "#4 eng Commentary")]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "refused"
    assert not Path(worker.side_path(env["video"], env["sub"])).exists()


def test_fullest_reference_wins(env):
    core.write(env["sub"], tgt_cues(env["base"], warp=lambda t: t + 10))
    forced_like = (ref_cues(env["base"][:25]), "#5 fre Forced-ish")
    env["refs"][:] = [forced_like, env["refs"][0]]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["reference"] == "#3 eng English"


def test_no_reference(env):
    core.write(env["sub"], tgt_cues(env["base"]))
    env["refs"][:] = [(ref_cues(env["base"][:10]), "#2 too short")]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "no_reference"


@pytest.mark.parametrize("which", ["missing_video", "not_srt", "own_output"])
def test_skipped(env, which):
    core.write(env["sub"], tgt_cues(env["base"]))
    video, sub = env["video"], env["sub"]
    if which == "missing_video":
        video += ".gone"
    elif which == "not_srt":
        sub = sub[:-4] + ".ass"; Path(sub).write_text("x", encoding="utf-8")
    else:
        sub = worker.side_path(video, sub); core.write(sub, tgt_cues(env["base"]))
    worker.process(video, sub)
    assert last_log(env)["status"] == "skipped"


def test_backfill_queues_only_subtitles_next_to_their_video(env):
    media = Path(env["video"]).parent
    core.write(env["sub"], tgt_cues(env["base"]))
    (media / "Show - S01E01.semsync.fr.srt").write_text("x", encoding="utf-8")   # our own output
    (media / "Orphan.fr.srt").write_text("x", encoding="utf-8")                  # no video
    worker.backfill(str(env["tmp"] / "Show"))
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in Path(worker.QUEUE).glob("*.job")]
    assert [(j["video"], j["sub"], j["origin"]) for j in jobs] == [(env["video"], env["sub"], "backfill")]


def test_bazarr_enqueue_is_atomic_and_standalone(tmp_path):
    """enqueue.py runs under Bazarr's own python: no dependency, writes next to itself."""
    script = tmp_path / "enqueue.py"
    script.write_bytes((ROOT / "bazarr" / "enqueue.py").read_bytes())
    r = subprocess.run([sys.executable, str(script), "/data/v é.mkv", "/data/v é.fr.srt", "87.5"],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    files = os.listdir(tmp_path / "queue")
    assert len(files) == 1 and files[0].endswith(".job")
    job = json.loads((tmp_path / "queue" / files[0]).read_text(encoding="utf-8"))
    assert job == {"video": "/data/v é.mkv", "sub": "/data/v é.fr.srt", "score": "87.5", "origin": "bazarr"}
