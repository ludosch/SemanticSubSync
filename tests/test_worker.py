"""Worker: file naming (as Jellyfin reads it), encodings, decisions, the state database, the
queue and the Bazarr hook. ffmpeg is not needed: the embedded tracks are synthetic."""
import json, os, subprocess, sys
from pathlib import Path

import pytest

from semantic_subsync import core, media, worker
from semantic_subsync.state import FIELDS, State
from synth import dialogue, fake_embed, ref_cues, tgt_cues

ROOT = Path(__file__).resolve().parents[1]
EN_TRACK = {"index": 3, "codec": "subrip", "text": True, "lang": "en", "kind": "normal", "title": "English"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A media folder with one video + downloaded subtitle; the model is the fake embedder and
    the video embeds one English text subtitle (editable through env["tracks"] / env["refs"])."""
    monkeypatch.setattr(worker, "LOG", str(tmp_path / "semsync.log"))
    monkeypatch.setattr(worker, "QUEUE", str(tmp_path / "queue"))
    monkeypatch.setattr(worker, "FAILED", str(tmp_path / "failed"))
    monkeypatch.setattr(worker, "DB", str(tmp_path / "state.db"))
    monkeypatch.setattr(worker, "OUTPUT", "replace")
    monkeypatch.setattr(core, "embed", fake_embed)
    folder = tmp_path / "Show" / "Season 1"
    folder.mkdir(parents=True)
    video = folder / "Show - S01E01.mkv"
    video.write_bytes(b"video")
    sub = folder / "Show - S01E01.fr.srt"
    base = dialogue(n=200, seed=21)
    tracks = [dict(EN_TRACK)]
    refs = [(ref_cues(base), "#3 en English")]
    monkeypatch.setattr(media, "probe_subtitles", lambda v: tracks)
    monkeypatch.setattr(media, "embedded_references", lambda v, wd, streams=None, tracks=None: refs)
    return dict(tmp=tmp_path, folder=folder, video=str(video), sub=str(sub), base=base, refs=refs, tracks=tracks)


def last_log(env):
    return json.loads(Path(worker.LOG).read_text(encoding="utf-8").splitlines()[-1])


def row(sub):
    st = State(worker.DB)
    try:
        return st.get(sub)
    finally:
        st.close()


def replaced_of(env, sub=None):
    return Path(worker.tagged_path(env["video"], sub or env["sub"], worker.REPLACED))


def write_late(env, sub=None, shift=10):
    core.write(sub or env["sub"], tgt_cues(env["base"], warp=lambda t: t + shift))
    return Path(sub or env["sub"]).read_bytes()


# --- names, as Jellyfin reads them -------------------------------------------------------------

def jellyfin_parse(video, sub):
    """Port of Jellyfin's Emby.Naming ExternalPathParser.ParseFile (10.10/10.11) for a subtitle
    next to `video`: the words after the video's name are read right to left; 'default',
    'forced' and 'foreign' anywhere in a word are flags, a language code is the language (the
    first one found), 'sdh' / 'cc' / 'hi' mark hearing impaired, and the other words make the
    track's title, shown first in the player ("replaced - French - SUBRIP - External")."""
    extra = os.path.basename(sub)[len(os.path.basename(os.path.splitext(video)[0])):-len(".srt")]
    title, lang, forced, hi = [], None, False, False
    for word in reversed(extra.split(".")[1:]):
        w = word.lower()
        if "default" in w:
            continue
        if "forced" in w or "foreign" in w:
            forced = True
        elif media.language_key(w) in media.LANGUAGE_KEY.values() and lang is None and w not in ("hi", "cc", "sdh"):
            lang = media.language_key(w)
        elif w in ("sdh", "cc", "hi"):
            hi = True
        else:
            title.insert(0, word)
    return {"title": ".".join(title) or None, "lang": lang, "forced": forced, "hi": hi}


@pytest.mark.parametrize("sub,lang,hi,forced", [
    ("Show - S01E01.fr.srt", "fr", False, False),
    ("Show - S01E01.fr.hi.srt", "fr", True, False),
    ("Show - S01E01.en.sdh.srt", "en", True, False),
    ("Show - S01E01.pt-BR.srt", "pt-br", False, False),
    ("Show - S01E01.fr.forced.srt", "fr", False, True),
])
def test_jellyfin_reads_our_files_as_the_same_language_and_kind(sub, lang, hi, forced):
    video = "/m/Show - S01E01.mkv"
    orig = jellyfin_parse(video, "/m/" + sub)
    assert orig == {"title": None, "lang": lang, "forced": forced, "hi": hi}
    for tag in (worker.REPLACED, worker.RESYNC):
        assert jellyfin_parse(video, worker.tagged_path(video, "/m/" + sub, tag)) == {**orig, "title": tag}


def test_our_tags_are_not_jellyfin_flags_nor_languages():
    for tag in worker.OWN_TAGS:
        assert tag == tag.lower()              # Jellyfin shows the title as written
        assert not any(f in tag for f in ("default", "forced", "foreign"))
        assert media.language_key(tag) not in media.LANGUAGE_KEY.values()


@pytest.mark.parametrize("lang", ["fr", "en", "fr.hi", "en.sdh", "de", "es", "it", "pt-BR"])
def test_correction_is_listed_before_the_replaced_download(lang):
    """Jellyfin lists external subtitles in file name order: the correction (which keeps the
    download's name) must come first, so that it is the one picked for the language."""
    sub = f"/m/Show - S01E01.{lang}.srt"
    assert sub < worker.tagged_path("/m/Show - S01E01.mkv", sub, worker.REPLACED)


@pytest.mark.parametrize("video,sub,expected", [
    ("/m/Show - S01E01.mkv", "/m/Show - S01E01.fr.srt", "/m/Show - S01E01.replaced.fr.srt"),
    ("/m/Show - S01E01.mkv", "/m/Show - S01E01.fr.hi.srt", "/m/Show - S01E01.replaced.fr.hi.srt"),
    ("/m/Film (2001).mp4", "/m/Film (2001).fr.forced.srt", "/m/Film (2001).replaced.fr.forced.srt"),
    ("/m/A.mkv", "/m/other name.fr.srt", "/m/other name.fr.replaced.srt"),      # not the video's stem
])
def test_tagged_path(video, sub, expected):
    assert worker.tagged_path(video, sub, worker.REPLACED) == expected


@pytest.mark.parametrize("name,own", [
    ("Show.fr.srt", False), ("Show.replaced.fr.srt", True), ("Show.resync.fr.hi.srt", True),
    ("Replaced Lives.fr.srt", False),
])
def test_is_own(name, own):
    assert worker.is_own("/m/" + name) is own


@pytest.mark.parametrize("name,lang,kind", [
    ("Show.fr.srt", "fr", "normal"), ("Show.fr.hi.srt", "fr", "hi"), ("Show.en.sdh.srt", "en", "hi"),
    ("Show.eng.srt", "en", "normal"), ("Show.fre.forced.srt", "fr", "forced"), ("Show.hi.srt", None, "hi"),
])
def test_language_and_kind_from_file_name(name, lang, kind):
    assert media.language_key(media.language_of(name)) == (lang or "") and media.kind_of(name) == kind


# --- encodings ---------------------------------------------------------------------------------

def test_read_srt_cp1252(tmp_path):
    p = tmp_path / "a.srt"
    # French on purpose, no language tag in the name: accented letters are where cp1252 and
    # UTF-8 differ (a real line: a few % of accented letters, as in French subtitles)
    line = "Ça a déjà été dit, mais il faut que tu le saches maintenant."
    p.write_bytes(f"1\n00:00:01,000 --> 00:00:02,000\n{line}\n".encode("cp1252"))
    assert media.read_srt(str(p))[0][2] == line


def test_read_srt_utf8_bom(tmp_path):
    p = tmp_path / "a.srt"
    p.write_bytes("﻿1\n00:00:01,000 --> 00:00:02,000\nÇa\n".encode("utf-8"))
    assert media.read_srt(str(p))[0][2] == "Ça"


# --- decisions and files, replace mode (default) -----------------------------------------------

def test_corrected_replaces_and_keeps_the_download_as_replaced(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    rec = last_log(env)
    assert rec["status"] == "corrected" and rec["reference"] == "#3 en English"
    assert replaced_of(env).read_bytes() == before                      # download kept, visible
    out = core.parse(env["sub"])
    assert abs(out[0][0] - env["base"][0][0]) < 0.05                    # the subtitle is fixed
    assert sorted(p.name for p in env["folder"].iterdir()) == [
        "Show - S01E01.fr.srt", "Show - S01E01.mkv", "Show - S01E01.replaced.fr.srt"]   # no .tmp, no side file
    r = row(env["sub"])
    assert r["status"] == "corrected" and r["output_path"] == env["sub"]
    assert r["replaced_path"] == str(replaced_of(env)) and r["lang"] == "fr" and r["kind"] == "normal"


def test_in_sync_touches_nothing(env):
    core.write(env["sub"], tgt_cues(env["base"]))
    before = Path(env["sub"]).read_bytes()
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "in_sync"
    assert Path(env["sub"]).read_bytes() == before and not replaced_of(env).exists()
    assert row(env["sub"])["output_path"] is None


def test_second_run_on_our_own_output_is_unchanged(env):
    write_late(env)
    worker.process(env["video"], env["sub"])
    fixed = Path(env["sub"]).read_bytes()
    worker.process(env["video"], env["sub"], origin="backfill")
    assert last_log(env)["status"] == "unchanged"
    assert Path(env["sub"]).read_bytes() == fixed and row(env["sub"])["runs"] == 1


def test_force_starts_again_from_the_download(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    worker.process(env["video"], env["sub"], force=True)
    rec = last_log(env)
    assert rec["status"] == "corrected"
    assert replaced_of(env).read_bytes() == before                      # not our own output
    assert row(env["sub"])["runs"] == 2


def test_new_download_over_our_correction_is_processed_again(env):
    write_late(env)
    worker.process(env["video"], env["sub"])
    core.write(env["sub"], tgt_cues(env["base"], text="new k{k}"))     # the downloader writes a good one
    new = Path(env["sub"]).read_bytes()
    worker.process(env["video"], env["sub"], origin="bazarr")
    assert last_log(env)["status"] == "in_sync"
    assert Path(env["sub"]).read_bytes() == new
    assert not replaced_of(env).exists()                                # the old download is gone


def test_new_late_download_replaces_the_old_replaced_file(env):
    write_late(env, shift=10)
    worker.process(env["video"], env["sub"])
    second = write_late(env, shift=-7)
    worker.process(env["video"], env["sub"], origin="bazarr")
    assert last_log(env)["status"] == "corrected"
    assert replaced_of(env).read_bytes() == second
    assert abs(core.parse(env["sub"])[0][0] - env["base"][0][0]) < 0.05


def test_replaced_video_resyncs_the_download_not_our_output(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    Path(env["video"]).write_bytes(b"another release")                   # upgraded by Sonarr / Radarr
    env["refs"][:] = [(ref_cues([[s + 3, e + 3, k] for s, e, k in env["base"]]), "#3 en English")]
    worker.process(env["video"], env["sub"])
    rec = last_log(env)
    assert rec["status"] == "corrected" and row(env["sub"])["runs"] == 2
    assert replaced_of(env).read_bytes() == before
    assert abs(core.parse(env["sub"])[0][0] - (env["base"][0][0] + 3)) < 0.05


def test_no_correction_any_more_gives_the_download_its_name_back(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    Path(env["video"]).write_bytes(b"another release")
    env["refs"][:] = [(ref_cues([[s + 10, e + 10, k] for s, e, k in env["base"]]), "#3 en English")]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "in_sync"
    assert Path(env["sub"]).read_bytes() == before and not replaced_of(env).exists()


def test_unsure_on_unrelated_reference(env):
    before = write_late(env)
    env["refs"][:] = [([[s, e, f"line k{k + 9999}"] for s, e, k in env["base"]], "#4 en Commentary")]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "unsure"
    assert Path(env["sub"]).read_bytes() == before and not replaced_of(env).exists()


def test_fullest_reference_wins(env):
    write_late(env)
    forced_like = (ref_cues(env["base"][:25]), "#5 fr Forced-ish")
    env["refs"][:] = [forced_like, env["refs"][0]]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["reference"] == "#3 en English"


def test_no_reference(env):
    core.write(env["sub"], tgt_cues(env["base"]))
    env["refs"][:] = [(ref_cues(env["base"][:10]), "#2 too short")]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "no_reference"


# --- the video already has this subtitle -------------------------------------------------------

@pytest.mark.parametrize("sub_name,track,redundant", [
    ("fr.srt", {"lang": "fr", "kind": "normal"}, True),           # same language and kind
    ("fr.hi.srt", {"lang": "fr", "kind": "hi"}, True),
    ("fr.hi.srt", {"lang": "fr", "kind": "normal"}, False),       # SDH wanted, the video has plain French
    ("fr.srt", {"lang": "fr", "kind": "hi"}, False),
    ("fr.srt", {"lang": "fr", "kind": "normal", "text": False, "codec": "hdmv_pgs_subtitle"}, False),  # image track
    ("en.srt", {"lang": "fr", "kind": "normal"}, False),          # MULTi release, French only embedded
    ("fr.srt", {"lang": "fr", "kind": "forced"}, False),
])
def test_redundant_when_the_video_embeds_the_same_subtitle(env, sub_name, track, redundant):
    sub = str(env["folder"] / f"Show - S01E01.{sub_name}")
    before = write_late(env, sub)
    env["tracks"][0]["lang"] = "de"                                      # base track: another language
    env["tracks"].append({**EN_TRACK, "index": 4, "title": "", **track})
    worker.process(env["video"], sub)
    rec = last_log(env)
    if redundant:
        assert rec["status"] == "redundant" and rec["reason"].startswith("embedded #4")
        assert Path(sub).read_bytes() == before
    else:
        assert rec["status"] == "corrected"


def test_every_external_subtitle_of_a_video_is_processed_on_its_own(env):
    subs = [str(env["folder"] / f"Show - S01E01.{s}.srt") for s in ("fr", "fr.hi", "es")]
    for s in subs:
        write_late(env, s)
    worker.backfill(str(env["tmp"] / "Show"))
    for job in sorted(Path(worker.QUEUE).glob("*.job")):
        j = json.loads(job.read_text(encoding="utf-8"))
        worker.process(j["video"], j["sub"], j["origin"])
    for s in subs:
        assert row(s)["status"] == "corrected"
        assert replaced_of(env, s).exists()


# --- side mode ---------------------------------------------------------------------------------

def test_side_mode_writes_resync_and_keeps_the_download(env, monkeypatch):
    monkeypatch.setattr(worker, "OUTPUT", "side")
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    side = Path(worker.side_path(env["video"], env["sub"]))
    assert side.name == "Show - S01E01.resync.fr.srt"
    assert Path(env["sub"]).read_bytes() == before and side.exists() and not replaced_of(env).exists()
    core.write(env["sub"], tgt_cues(env["base"]))                       # new download, in sync
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "in_sync" and not side.exists()


def test_switching_from_replace_to_side_restores_the_download(env, monkeypatch):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    monkeypatch.setattr(worker, "OUTPUT", "side")
    worker.process(env["video"], env["sub"])
    assert Path(env["sub"]).read_bytes() == before and not replaced_of(env).exists()
    assert Path(worker.side_path(env["video"], env["sub"])).exists()


# --- skipped, queue, state ---------------------------------------------------------------------

@pytest.mark.parametrize("which", ["missing_video", "not_srt", "replaced", "resync"])
def test_skipped(env, which):
    core.write(env["sub"], tgt_cues(env["base"]))
    video, sub = env["video"], env["sub"]
    if which == "missing_video":
        video += ".gone"
    elif which == "not_srt":
        sub = sub[:-4] + ".ass"; Path(sub).write_text("x", encoding="utf-8")
    else:
        tag = {"replaced": worker.REPLACED, "resync": worker.RESYNC}[which]
        sub = worker.tagged_path(video, sub, tag); core.write(sub, tgt_cues(env["base"]))
    worker.process(video, sub)
    assert last_log(env)["status"] == "skipped"


def test_backfill_queues_only_downloaded_subtitles_next_to_their_video(env):
    core.write(env["sub"], tgt_cues(env["base"]))
    for tag in worker.OWN_TAGS:
        Path(worker.tagged_path(env["video"], env["sub"], tag)).write_text("x", encoding="utf-8")
    (env["folder"] / "Orphan.fr.srt").write_text("x", encoding="utf-8")                  # no video
    worker.backfill(str(env["tmp"] / "Show"))
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in Path(worker.QUEUE).glob("*.job")]
    assert [(j["video"], j["sub"], j["origin"]) for j in jobs] == [(env["video"], env["sub"], "backfill")]


def test_state_records_every_field(env):
    write_late(env)
    worker.process(env["video"], env["sub"], origin="bazarr", score="87.5")
    r = row(env["sub"])
    assert set(r) == set(FIELDS)
    filled = {k for k, v in r.items() if v is not None}
    assert filled == set(FIELDS) - {"reason"}
    assert json.loads(r["settings"])["extra_lines"] == "drop" and r["score"] == "87.5"


# --- log ---------------------------------------------------------------------------------------

def test_log_of_a_correction_has_segments_hashes_and_settings(env):
    write_late(env)
    worker.process(env["video"], env["sub"])
    rec = last_log(env)
    assert rec["seg"] and all(abs(s["offset"] + 10) < 0.1 for s in rec["seg"])
    assert set(rec["seg"][0]) == {"from", "to", "offset", "drift_ppm"}
    assert len(rec["input_sha256"]) == len(rec["output_sha256"]) == 64
    assert rec["input_sha256"] != rec["output_sha256"] and rec["settings"]["extra_lines"] == "drop"
    assert json.loads(row(env["sub"])["seg"]) == rec["seg"]


def test_history_shows_every_run_of_a_subtitle(env, capsys):
    write_late(env)
    worker.process(env["video"], env["sub"])
    worker.process(env["video"], env["sub"])
    capsys.readouterr()
    worker.history(["s01e01"])
    out = capsys.readouterr().out
    assert out.startswith(env["sub"] + "\n")                            # full path: series, season, language
    assert "corrected" in out and "unchanged" in out and "offset -10.00 s" in out
    worker.history(["S02E05"])
    assert capsys.readouterr().out == ""


def test_history_and_status_need_every_word(env, capsys):
    """Two series with an S01E01: one word lists both, apart; more words single one out."""
    other = env["tmp"] / "Other" / "Season 1"
    other.mkdir(parents=True)
    video2 = other / "Other - S01E01.mkv"
    video2.write_bytes(b"other video")
    sub2 = str(other / "Other - S01E01.fr.srt")
    for v, s in ((env["video"], env["sub"]), (str(video2), sub2)):
        write_late(env, s)
        worker.process(v, s)
    capsys.readouterr()
    worker.history(["S01E01"])
    out = capsys.readouterr().out
    assert out.count("corrected") == 2 and env["sub"] in out and sub2 in out
    assert "2 subtitles match" in out
    worker.history(["other", "s01e01", "fr"])
    out = capsys.readouterr().out
    assert sub2 in out and env["sub"] not in out and "match" not in out
    worker.status(["Other", "S01E01"])
    out = capsys.readouterr().out
    assert sub2 in out and env["sub"] not in out and '"corrected": 1' in out


def test_log_past_its_limit_loses_its_oldest_entries_only(env):
    for n in range(2000):
        worker.log({"status": "test", "n": n})
    size = os.path.getsize(worker.LOG)
    worker.trim_log(max_bytes=size // 4)
    lines = Path(worker.LOG).read_text(encoding="utf-8").splitlines()
    assert os.path.getsize(worker.LOG) <= size // 8
    assert [json.loads(x)["n"] for x in lines] == list(range(2000 - len(lines), 2000))   # whole, newest
    assert not list(Path(env["tmp"]).glob("semsync.log.*"))             # no archive copy
    worker.trim_log(max_bytes=0)                                        # 0 = no limit
    assert len(Path(worker.LOG).read_text(encoding="utf-8").splitlines()) == len(lines)


def test_log_limit_is_applied_on_every_write(env, monkeypatch):
    monkeypatch.setattr(worker, "LOG_MAX_BYTES", 4000)
    for n in range(500):
        worker.log({"status": "test", "n": n})
        assert os.path.getsize(worker.LOG) <= 4000
    assert json.loads(Path(worker.LOG).read_text(encoding="utf-8").splitlines()[-1])["n"] == 499


def test_bazarr_enqueue_is_atomic_and_standalone(tmp_path):
    """enqueue.py runs under Bazarr's own python: no dependency, writes next to itself."""
    script = tmp_path / "enqueue.py"
    script.write_bytes((ROOT / "integrations" / "bazarr" / "enqueue.py").read_bytes())
    r = subprocess.run([sys.executable, str(script), "/data/v é.mkv", "/data/v é.fr.srt", "87.5"],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    files = os.listdir(tmp_path / "queue")
    assert len(files) == 1 and files[0].endswith(".job")
    job = json.loads((tmp_path / "queue" / files[0]).read_text(encoding="utf-8"))
    assert job == {"video": "/data/v é.mkv", "sub": "/data/v é.fr.srt", "score": "87.5", "origin": "bazarr"}
