"""Worker: file naming (as Jellyfin reads it), encodings, decisions, the state database, the
queue and the Bazarr hook. ffmpeg is not needed: the embedded tracks are synthetic."""
import json, os, subprocess, sys
from pathlib import Path

import pytest

from semantic_subsync import core, media, worker
from semantic_subsync.state import FIELDS, State
from synth import dialogue, fake_embed, ref_cues, tgt_cues, use_fake_model

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
    monkeypatch.setattr(worker, "KEEP_DOWNLOAD", "visible")
    use_fake_model(monkeypatch)
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


def kept_of(env, sub=None, keep=None):
    return Path(worker.kept_path(sub or env["sub"], keep))


def legacy_of(env, sub=None):
    """Where versions up to 0.12 kept the download."""
    return Path(worker.tagged_path(env["video"], sub or env["sub"], worker.REPLACED))


def write_late(env, sub=None, shift=10):
    core.write(sub or env["sub"], tgt_cues(env["base"], warp=lambda t: t + shift))
    return Path(sub or env["sub"]).read_bytes()


# --- names, as Jellyfin reads them -------------------------------------------------------------

# Language words Jellyfin recognises in a file name (ISO 639-1, ISO 639-2 B/T and English names,
# as in its culture list), written out here rather than taken from media.LANGUAGE_KEY so that a
# wrong entry there makes the tests below fail.
JELLYFIN_LANGUAGES = set("""
en eng english fr fre fra french es spa spanish de ger deu german it ita italian pt por portuguese
pt-br pob nl dut nld dutch sv swe swedish da dan danish no nor norwegian nb nob fi fin finnish
pl pol polish cs cze ces czech sk slo slk slovak hu hun hungarian ro rum ron romanian hr hrv croatian
sl slv slovenian sr srp serbian bs bos bosnian bg bul bulgarian mk mac mkd macedonian ru rus russian
uk ukr ukrainian be bel belarusian el gre ell greek tr tur turkish he heb hebrew ar ara arabic
fa per fas persian hi hin hindi th tha thai vi vie vietnamese id ind indonesian ms may msa malay
ja jpn japanese ko kor korean zh chi zho chinese ca cat catalan eu baq eus basque gl glg galician
et est estonian lv lav latvian lt lit lithuanian is ice isl icelandic
""".split())


def jellyfin_parse(video, sub):
    """Port of Jellyfin's Emby.Naming ExternalPathParser.ParseFile (10.10/10.11) for a subtitle
    next to `video`: the words after the video's name are read right to left; 'default',
    'forced' and 'foreign' anywhere in a word are flags, a language word is the language (the
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
        elif w in JELLYFIN_LANGUAGES and lang is None and w not in ("hi", "cc", "sdh"):
            lang = w
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
    assert jellyfin_parse(video, worker.kept_path("/m/" + sub, "visible")) == {**orig, "title": worker.UNTOUCHED}
    for tag in (worker.REPLACED, worker.RESYNC):
        assert jellyfin_parse(video, worker.tagged_path(video, "/m/" + sub, tag)) == {**orig, "title": tag}


def test_our_tags_are_not_jellyfin_flags_nor_languages():
    for tag in worker.OWN_TAGS:
        assert tag == tag.lower()              # Jellyfin shows the title as written
        assert not any(f in tag for f in ("default", "forced", "foreign"))
        assert tag not in JELLYFIN_LANGUAGES and media.language_key(tag) not in media.LANGUAGE_KEY.values()


def jellyfin_sort_key(name):
    """Jellyfin (12.1) lists the files of a folder with `OrderBy(x => x)` (DirectoryService.
    GetFilePaths): the server culture's string order, ICU en-US in the official images. Measured
    on a real server: punctuation and symbols ('.', '-', '~') come before digits, digits before
    letters, letters alphabetically whatever their case. This keeps that first level."""
    return [(0, c) if not c.isalnum() else (1, c) if c.isdigit() else (2, c.lower()) for c in name]


# every language word a subtitle may carry: the worker's list, ISO 639-2 codes and English names
ALL_LANGUAGES = sorted(set(media.LANGUAGE_KEY) | JELLYFIN_LANGUAGES)


@pytest.mark.parametrize("lang", ALL_LANGUAGES)
def test_the_correction_is_listed_before_the_kept_download_in_every_language(lang):
    """For a language, Jellyfin plays the first external subtitle in file name order. The
    correction keeps the download's exact name and the kept download gains "untouched" last,
    after the language and flags: the correction always comes first (culture-aware or ordinal order),
    and the kept download shows as the same language and kind, titled "untouched"."""
    video = "/m/Show - S01E01.mkv"
    for kind in ("", ".hi", ".sdh", ".cc", ".forced", ".default", ".hi.forced", ".Netflix", ".hi.WEB-DL"):
        sub = f"/m/Show - S01E01.{lang}{kind}.srt"
        kept = worker.kept_path(sub, "visible")
        assert kept == f"/m/Show - S01E01.{lang}{kind}.untouched.srt"
        assert jellyfin_sort_key(sub) < jellyfin_sort_key(kept) and sub < kept
        orig = jellyfin_parse(video, sub)
        assert jellyfin_parse(video, kept) == {**orig, "title": ".".join(filter(None, [orig["title"], worker.UNTOUCHED]))}
        assert worker.is_own(kept, video) and worker.is_own(kept) and not worker.is_own(sub, video)


def test_the_sort_model_reproduces_the_bug_of_the_old_name():
    """Measured on Jellyfin 12.1: 'Show.replaced.ru.srt' was played instead of the correction
    'Show.ru.srt' ("replaced" < "ru"), while French was fine ("fr" < "replaced")."""
    for lang, before in (("ru", True), ("zh", True), ("sv", True), ("fr", False), ("en", False)):
        sub = f"/m/Show.{lang}.srt"
        legacy = worker.tagged_path("/m/Show.mkv", sub, worker.REPLACED)
        assert (jellyfin_sort_key(legacy) < jellyfin_sort_key(sub)) is before
        # and a symbol in front of the tag would not help: '~' sorts before letters
        assert jellyfin_sort_key(f"/m/Show.{lang}.~untouched.srt") < jellyfin_sort_key(sub)


# extensions read as subtitles: Jellyfin (NamingOptions.SubtitleFileExtensions, plus .idx) and Bazarr
JELLYFIN_SUBTITLE_EXT = {".ass", ".mks", ".sami", ".smi", ".srt", ".ssa", ".sub", ".sup", ".vtt", ".idx"}
BAZARR_SUBTITLE_EXT = {".srt", ".sub", ".smi", ".txt", ".ssa", ".ass", ".mpl", ".vtt", ".sup", ".idx"}


@pytest.mark.parametrize("sub", ["/m/Show.fr.srt", "/m/Show.fr.hi.srt", "/m/Show.ru.forced.srt", "/m/Show.srt"])
def test_the_hidden_download_is_not_read_as_a_subtitle(sub):
    hidden = worker.kept_path(sub, "hidden")
    assert hidden == sub + ".orig" and worker.is_own(hidden) and worker.is_own(hidden, "/m/Show.mkv")
    assert os.path.splitext(hidden)[1] not in JELLYFIN_SUBTITLE_EXT | BAZARR_SUBTITLE_EXT


def test_the_correction_is_written_under_the_download_name(env):
    write_late(env)
    rec = worker.process(env["video"], env["sub"])
    assert rec["output_path"] == env["sub"] and rec["replaced_path"] == str(kept_of(env))


@pytest.mark.parametrize("video,sub,expected", [
    ("/m/Show - S01E01.mkv", "/m/Show - S01E01.fr.srt", "/m/Show - S01E01.replaced.fr.srt"),
    ("/m/Show - S01E01.mkv", "/m/Show - S01E01.fr.hi.srt", "/m/Show - S01E01.replaced.fr.hi.srt"),
    ("/m/Film (2001).mp4", "/m/Film (2001).fr.forced.srt", "/m/Film (2001).replaced.fr.forced.srt"),
    # not named after the video: the tag still goes before the language and flags
    ("/m/A.mkv", "/m/other name.fr.srt", "/m/other name.replaced.fr.srt"),
    ("/m/A.mkv", "/m/Other.2024.pt-BR.hi.srt", "/m/Other.2024.replaced.pt-BR.hi.srt"),
    ("/m/A.mkv", "/m/Other.srt", "/m/Other.replaced.srt"),
])
def test_tagged_path(video, sub, expected):
    assert worker.tagged_path(video, sub, worker.REPLACED) == expected


@pytest.mark.parametrize("name,video,own", [
    ("Show.fr.srt", None, False), ("Show.replaced.fr.srt", None, True), ("Show.resync.fr.hi.srt", None, True),
    ("Show.fr.untouched.srt", None, True), ("Show.fr.hi.untouched.srt", "Show.mkv", True),
    ("Show.fr.srt.orig", None, True), ("Show.fr.srt.orig", "Show.mkv", True),
    ("Replaced Lives.fr.srt", None, False),
    ("The.Replaced.2024.fr.srt", "The.Replaced.2024.mkv", False),     # a word of the title, not our tag
    ("The.Replaced.2024.fr.srt", None, False),
    ("The.Replaced.2024.replaced.fr.srt", "The.Replaced.2024.mkv", True),
    ("Untouched.2024.fr.srt", "Untouched.2024.mkv", False), ("Untouched.fr.srt", None, False),
    ("Resync.fr.srt", "Resync.mkv", False),
    ("other name.fr.replaced.srt", "A.mkv", True),                     # as earlier versions named it
])
def test_is_own(name, video, own):
    assert worker.is_own("/m/" + name, video and "/m/" + video) is own


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

def test_corrected_replaces_and_keeps_the_download_as_untouched(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    rec = last_log(env)
    assert rec["status"] == "corrected" and rec["reference"] == "#3 en English"
    assert kept_of(env).read_bytes() == before                      # download kept, visible
    out = core.parse(env["sub"])
    assert abs(out[0][0] - env["base"][0][0]) < 0.05                    # the subtitle is fixed
    assert sorted(p.name for p in env["folder"].iterdir()) == [
        "Show - S01E01.fr.srt", "Show - S01E01.fr.untouched.srt", "Show - S01E01.mkv"]   # no .tmp, no side file
    r = row(env["sub"])
    assert r["status"] == "corrected" and r["output_path"] == env["sub"]
    assert r["replaced_path"] == str(kept_of(env)) and r["lang"] == "fr" and r["kind"] == "normal"


def test_in_sync_touches_nothing(env):
    core.write(env["sub"], tgt_cues(env["base"]))
    before = Path(env["sub"]).read_bytes()
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "in_sync"
    assert Path(env["sub"]).read_bytes() == before and not kept_of(env).exists()
    assert row(env["sub"])["output_path"] is None


def test_second_run_on_our_own_output_is_unchanged(env):
    write_late(env)
    worker.process(env["video"], env["sub"])
    fixed = Path(env["sub"]).read_bytes()
    log_size = os.path.getsize(worker.LOG)
    rec = worker.process(env["video"], env["sub"], origin="backfill")
    assert rec["status"] == "unchanged" and rec["last"] == "corrected"
    assert Path(env["sub"]).read_bytes() == fixed and row(env["sub"])["runs"] == 1
    assert os.path.getsize(worker.LOG) == log_size                      # re-runs do not fill the log


def test_force_starts_again_from_the_download(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    worker.process(env["video"], env["sub"], force=True)
    rec = last_log(env)
    assert rec["status"] == "corrected"
    assert kept_of(env).read_bytes() == before                      # not the worker's own output
    assert row(env["sub"])["runs"] == 2


def test_new_download_over_our_correction_is_processed_again(env):
    write_late(env)
    worker.process(env["video"], env["sub"])
    core.write(env["sub"], tgt_cues(env["base"], text="new k{k}"))     # the downloader writes a good one
    new = Path(env["sub"]).read_bytes()
    worker.process(env["video"], env["sub"], origin="bazarr")
    assert last_log(env)["status"] == "in_sync"
    assert Path(env["sub"]).read_bytes() == new
    assert not kept_of(env).exists()                                # the old download is gone


def test_new_late_download_replaces_the_old_replaced_file(env):
    write_late(env, shift=10)
    worker.process(env["video"], env["sub"])
    second = write_late(env, shift=-7)
    worker.process(env["video"], env["sub"], origin="bazarr")
    assert last_log(env)["status"] == "corrected"
    assert kept_of(env).read_bytes() == second
    assert abs(core.parse(env["sub"])[0][0] - env["base"][0][0]) < 0.05


def test_replaced_video_resyncs_the_download_not_our_output(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    Path(env["video"]).write_bytes(b"another release")                   # upgraded by Sonarr / Radarr
    env["refs"][:] = [(ref_cues([[s + 3, e + 3, k] for s, e, k in env["base"]]), "#3 en English")]
    worker.process(env["video"], env["sub"])
    rec = last_log(env)
    assert rec["status"] == "corrected" and row(env["sub"])["runs"] == 2
    assert kept_of(env).read_bytes() == before
    assert abs(core.parse(env["sub"])[0][0] - (env["base"][0][0] + 3)) < 0.05


def test_no_correction_any_more_gives_the_download_its_name_back(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    Path(env["video"]).write_bytes(b"another release")
    env["refs"][:] = [(ref_cues([[s + 10, e + 10, k] for s, e, k in env["base"]]), "#3 en English")]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "in_sync"
    assert Path(env["sub"]).read_bytes() == before and not kept_of(env).exists()


def test_unsure_on_unrelated_reference(env):
    before = write_late(env)
    env["refs"][:] = [([[s, e, f"line k{k + 9999}"] for s, e, k in env["base"]], "#4 en Commentary")]
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "unsure"
    assert Path(env["sub"]).read_bytes() == before and not kept_of(env).exists()


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
        assert kept_of(env, s).exists()


# --- side mode ---------------------------------------------------------------------------------

def test_side_mode_writes_resync_and_keeps_the_download(env, monkeypatch):
    monkeypatch.setattr(worker, "OUTPUT", "side")
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    side = Path(worker.side_path(env["video"], env["sub"]))
    assert side.name == "Show - S01E01.resync.fr.default.srt"
    assert Path(env["sub"]).read_bytes() == before and side.exists() and not kept_of(env).exists()
    core.write(env["sub"], tgt_cues(env["base"]))                       # new download, in sync
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "in_sync" and not side.exists()


def test_switching_from_replace_to_side_restores_the_download(env, monkeypatch):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    monkeypatch.setattr(worker, "OUTPUT", "side")
    worker.process(env["video"], env["sub"])
    assert Path(env["sub"]).read_bytes() == before and not kept_of(env).exists()
    assert Path(worker.side_path(env["video"], env["sub"])).exists()


# --- the kept download: hidden, names of earlier versions ------------------------------------

def as_earlier_version(env):
    """Turn the files of the current run into what versions up to 0.12 left: the download kept
    as '<video>.replaced.<lang>.srt', recorded so in state.db."""
    legacy = legacy_of(env)
    os.replace(kept_of(env), legacy)
    st = State(worker.DB)
    st.update(env["sub"], replaced_path=str(legacy))
    st.close()
    return legacy


def test_hidden_keeps_the_download_under_a_name_no_player_reads(env, monkeypatch):
    monkeypatch.setattr(worker, "KEEP_DOWNLOAD", "hidden")
    before = write_late(env)
    rec = worker.process(env["video"], env["sub"])
    hidden = Path(env["sub"] + ".orig")
    assert rec["status"] == "corrected" and rec["replaced_path"] == str(hidden) and hidden.read_bytes() == before
    assert sorted(p.name for p in env["folder"].iterdir()) == [
        "Show - S01E01.fr.srt", "Show - S01E01.fr.srt.orig", "Show - S01E01.mkv"]
    assert worker.process(env["video"], env["sub"])["status"] == "unchanged"
    worker.process(env["video"], env["sub"], force=True)                # starts again from the hidden download
    assert last_log(env)["input_sha256"] == rec["input_sha256"] and hidden.read_bytes() == before
    Path(env["video"]).write_bytes(b"another release")                   # no correction needed any more
    env["refs"][:] = [(ref_cues([[s + 10, e + 10, k] for s, e, k in env["base"]]), "#3 en English")]
    assert worker.process(env["video"], env["sub"])["status"] == "in_sync"
    assert Path(env["sub"]).read_bytes() == before and not hidden.exists()


@pytest.mark.parametrize("keep", ["visible", "hidden"])
def test_the_name_of_an_earlier_version_is_renamed_without_a_new_run(env, monkeypatch, keep):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    fixed = Path(env["sub"]).read_bytes()
    legacy = as_earlier_version(env)
    monkeypatch.setattr(worker, "KEEP_DOWNLOAD", keep)
    rec = worker.process(env["video"], env["sub"], origin="backfill")
    assert rec["status"] == "unchanged" and rec["renamed"] == [str(legacy), str(kept_of(env))]
    assert files_of(env["folder"]) == {Path(env["sub"]).name: fixed, kept_of(env).name: before}
    r = row(env["sub"])
    assert r["replaced_path"] == str(kept_of(env)) and r["runs"] == 1 and r["status"] == "corrected"
    assert last_log(env)["renamed"] == rec["renamed"]                  # a file changed: in the history
    log_size = os.path.getsize(worker.LOG)
    rec = worker.process(env["video"], env["sub"])
    assert rec["status"] == "unchanged" and "renamed" not in rec and os.path.getsize(worker.LOG) == log_size


def test_switching_between_visible_and_hidden_renames_on_the_next_check(env, monkeypatch):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    fixed = Path(env["sub"]).read_bytes()
    for keep in ("hidden", "visible", "hidden"):
        monkeypatch.setattr(worker, "KEEP_DOWNLOAD", keep)
        rec = worker.process(env["video"], env["sub"])
        assert rec["status"] == "unchanged" and rec["renamed"][1] == str(kept_of(env))
        assert files_of(env["folder"]) == {Path(env["sub"]).name: fixed, kept_of(env).name: before}
        assert row(env["sub"])["replaced_path"] == str(kept_of(env)) and row(env["sub"])["runs"] == 1


def test_the_name_of_an_earlier_version_with_state_lost(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    fixed = Path(env["sub"]).read_bytes()
    as_earlier_version(env)
    os.remove(worker.DB)
    rec = worker.process(env["video"], env["sub"])
    assert rec["status"] == "corrected" and rec["input_sha256"] == row(env["sub"])["input_sha256"]
    assert files_of(env["folder"]) == {Path(env["sub"]).name: fixed, kept_of(env).name: before}


def test_a_new_download_over_a_correction_of_an_earlier_version(env):
    write_late(env, shift=10)
    worker.process(env["video"], env["sub"])
    as_earlier_version(env)
    second = write_late(env, shift=-7)
    rec = worker.process(env["video"], env["sub"], origin="bazarr")
    assert rec["status"] == "corrected" and kept_of(env).read_bytes() == second
    assert not legacy_of(env).exists() and not list(env["folder"].glob("*.bak"))


@pytest.mark.parametrize("content", ["download", "unknown"])
def test_a_copy_under_an_earlier_name_beside_the_current_one(env, content):
    """Both names exist (a copy restored by hand...): the current one is the download; the other
    is removed when it holds the download, else kept aside."""
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    fixed = Path(env["sub"]).read_bytes()
    legacy_of(env).write_bytes(before if content == "download" else b"something else")
    rec = worker.process(env["video"], env["sub"])
    assert rec["status"] == "corrected" and not legacy_of(env).exists()
    assert files_of(env["folder"]) == {Path(env["sub"]).name: fixed, kept_of(env).name: before} | (
        {} if content == "download" else {Path(rec["kept_aside"][0]).name: b"something else"})
    assert worker.process(env["video"], env["sub"])["status"] == "unchanged"


def test_switching_to_side_restores_a_download_kept_by_an_earlier_version(env, monkeypatch):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    as_earlier_version(env)
    monkeypatch.setattr(worker, "OUTPUT", "side")
    worker.process(env["video"], env["sub"])
    assert Path(env["sub"]).read_bytes() == before and not legacy_of(env).exists() and not kept_of(env).exists()
    assert Path(worker.side_path(env["video"], env["sub"])).exists()


# --- model -------------------------------------------------------------------------------------

@pytest.fixture
def models_used(env, monkeypatch):
    used = []
    monkeypatch.setattr(core, "embed", lambda texts, model=None: used.append(model) or fake_embed(texts))
    return used


def test_default_model_is_used_and_recorded(env, models_used):
    write_late(env)
    worker.process(env["video"], env["sub"])
    r = row(env["sub"])
    assert set(models_used) == {core.DEFAULT_MODEL} and r["model"] == core.DEFAULT_MODEL and r["model_choice"] is None
    assert json.loads(r["settings"])["model"] == core.DEFAULT_MODEL


def test_model_chosen_by_hand_is_kept_for_later_runs(env, models_used):
    write_late(env)
    worker.process(env["video"], env["sub"])
    worker.process(env["video"], env["sub"], model="minilm")             # no --force needed: other model
    assert last_log(env)["status"] == "corrected" and models_used[-1] == "minilm"
    assert row(env["sub"])["model_choice"] == "minilm"
    write_late(env, shift=20)                                           # a new download later
    worker.process(env["video"], env["sub"], origin="bazarr")
    assert models_used[-1] == "minilm" and row(env["sub"])["model"] == "minilm"


def test_same_model_by_hand_on_an_unchanged_subtitle_is_unchanged(env, models_used):
    write_late(env)
    worker.process(env["video"], env["sub"], model="minilm")
    n = len(models_used)
    assert worker.process(env["video"], env["sub"], model="minilm")["status"] == "unchanged" and len(models_used) == n


def test_default_drops_the_choice(env, models_used):
    write_late(env)
    worker.process(env["video"], env["sub"], model="minilm")
    worker.process(env["video"], env["sub"], model="default")
    r = row(env["sub"])
    assert models_used[-1] == core.DEFAULT_MODEL and r["model"] == core.DEFAULT_MODEL and r["model_choice"] is None


def test_unknown_model_is_refused_before_anything(env):
    write_late(env)
    with pytest.raises(ValueError, match="unknown model"):
        worker.process(env["video"], env["sub"], model="bert")
    assert row(env["sub"]) is None


# --- skipped, queue, state ---------------------------------------------------------------------

@pytest.mark.parametrize("which", ["missing_video", "not_srt", "untouched", "replaced", "resync"])
def test_skipped(env, which):
    core.write(env["sub"], tgt_cues(env["base"]))
    video, sub = env["video"], env["sub"]
    if which == "missing_video":
        video += ".gone"
    elif which == "not_srt":
        sub = sub[:-4] + ".ass"; Path(sub).write_text("x", encoding="utf-8")
    elif which == "untouched":
        sub = worker.kept_path(sub); core.write(sub, tgt_cues(env["base"]))
    else:
        tag = {"replaced": worker.REPLACED, "resync": worker.RESYNC}[which]
        sub = worker.tagged_path(video, sub, tag); core.write(sub, tgt_cues(env["base"]))
    worker.process(video, sub)
    assert last_log(env)["status"] == "skipped"


def test_backfill_queues_only_downloaded_subtitles_next_to_their_video(env):
    core.write(env["sub"], tgt_cues(env["base"]))
    for tag in worker.OWN_TAGS:
        Path(worker.tagged_path(env["video"], env["sub"], tag)).write_text("x", encoding="utf-8")
    for keep in ("visible", "hidden"):
        Path(worker.kept_path(env["sub"], keep)).write_text("x", encoding="utf-8")
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
    assert filled == set(FIELDS) - {"reason", "model_choice", "pending"}
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
    assert "corrected" in out and "unchanged" not in out and "offset -10.00 s" in out   # unchanged: stdout only
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
    assert not [p for p in Path(env["tmp"]).glob("*semsync.log.*") if p.suffix != ".lock"]   # no archive, no temp copy
    worker.trim_log(max_bytes=0)                                        # 0 = no limit
    assert len(Path(worker.LOG).read_text(encoding="utf-8").splitlines()) == len(lines)


def test_log_limit_is_applied_on_every_write(env, monkeypatch):
    monkeypatch.setattr(worker, "LOG_MAX_BYTES", 4000)
    for n in range(500):
        worker.log({"status": "test", "n": n})
        assert os.path.getsize(worker.LOG) <= 4000
    assert json.loads(Path(worker.LOG).read_text(encoding="utf-8").splitlines()[-1])["n"] == 499


def run_enqueue(tmp_path, *args, semsync_dir=None):
    script = tmp_path / "enqueue.py"
    script.write_bytes((ROOT / "integrations" / "bazarr" / "enqueue.py").read_bytes())
    env = {k: v for k, v in os.environ.items() if k != "SEMSYNC_DIR"} | {"PYTHONIOENCODING": "utf-8"}
    if semsync_dir:
        env["SEMSYNC_DIR"] = str(semsync_dir)
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, encoding="utf-8",
                          env=env)


def queued(folder):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).iterdir())]


def test_bazarr_enqueue_is_atomic_and_standalone(tmp_path):
    """enqueue.py runs under Bazarr's own python: no dependency, writes next to itself."""
    r = run_enqueue(tmp_path, "/data/v é.mkv", "/data/v é.fr.srt", "87.5")
    assert r.returncode == 0, r.stderr
    files = os.listdir(tmp_path / "queue")
    assert len(files) == 1 and files[0].endswith(".job")
    assert queued(tmp_path / "queue") == [
        {"video": "/data/v é.mkv", "sub": "/data/v é.fr.srt", "score": "87.5", "origin": "bazarr"}]


def test_bazarr_enqueue_without_score_and_with_semsync_dir(tmp_path):
    r = run_enqueue(tmp_path, "/data/v.mkv", "/data/v.fr.srt", semsync_dir=tmp_path / "elsewhere")
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "queue").exists()
    assert queued(tmp_path / "elsewhere" / "queue") == [
        {"video": "/data/v.mkv", "sub": "/data/v.fr.srt", "score": "", "origin": "bazarr"}]
    assert run_enqueue(tmp_path, "/data/v.mkv", "/data/v.fr.srt", "").returncode == 0      # empty {{score}}


@pytest.mark.parametrize("args", [[], ["/data/v.mkv"], ["", "/data/v.fr.srt"]])
def test_bazarr_enqueue_without_paths_says_how_to_call_it(tmp_path, args):
    r = run_enqueue(tmp_path, *args)
    assert r.returncode != 0 and "usage" in r.stderr and "Traceback" not in r.stderr
    assert not (tmp_path / "queue").exists()


def test_one_prints_its_decision_as_the_last_stdout_line(env, monkeypatch, capsys):
    """What the Jellyfin plugin reads: `worker one VIDEO SUB` ends with one JSON line on stdout.
    It also runs where os.nice does not exist (Windows)."""
    monkeypatch.delattr(os, "nice", raising=False)
    monkeypatch.setattr(worker, "BASE", str(env["tmp"]))
    write_late(env)
    monkeypatch.setattr(sys, "argv", ["semantic-subsync-worker", "one", env["video"], env["sub"]])
    worker.main()
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rec["status"] == "corrected" and rec["sub"] == env["sub"] and rec["output_path"] == env["sub"]


def test_prepare_loads_the_default_model_and_prints_ready(monkeypatch, capsys):
    """What the Jellyfin plugin runs once the engine is installed: the model is downloaded then."""
    monkeypatch.delattr(os, "nice", raising=False)
    seen = []
    monkeypatch.setattr(worker.core, "embed", lambda texts, model=None: seen.append(model))
    monkeypatch.setattr(sys, "argv", ["semantic-subsync-worker", "prepare"])
    worker.main()
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rec["status"] == "ready" and seen == [worker.core.DEFAULT_MODEL] == [rec["model"]]


@pytest.mark.parametrize("sub,expected", [
    ("Show.fr.srt", "Show.resync.fr.default.srt"),
    ("Show.fr.hi.srt", "Show.resync.fr.hi.default.srt"),
    ("Show.fr.forced.srt", "Show.resync.fr.forced.srt"),          # would be picked over the full subtitles
    ("Show.fr.default.srt", "Show.resync.fr.default.srt"),         # flagged once
    ("Other.fr.srt", "Other.resync.fr.default.srt"),
])
def test_side_path_flags_the_correction_as_default(sub, expected):
    assert worker.side_path("/m/Show.mkv", "/m/" + sub) == "/m/" + expected
    assert worker.is_own("/m/" + expected)


def test_a_deleted_or_edited_correction_is_written_again(env, monkeypatch):
    monkeypatch.setattr(worker, "OUTPUT", "side")
    write_late(env)
    worker.process(env["video"], env["sub"])
    side = Path(worker.side_path(env["video"], env["sub"]))
    good = side.read_bytes()
    assert worker.process(env["video"], env["sub"])["status"] == "unchanged"
    side.unlink()
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "corrected" and side.read_bytes() == good
    side.write_bytes(b"edited by hand")
    worker.process(env["video"], env["sub"])
    assert last_log(env)["status"] == "corrected" and side.read_bytes() == good


# --- a download is never lost ------------------------------------------------------------------

def files_of(folder):
    return {p.name: p.read_bytes() for p in Path(folder).iterdir() if p.suffix != ".mkv"}


def test_lost_state_keeps_the_download_and_its_correction(env):
    """state.db deleted: the kept file is the download, the subtitle its correction."""
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    fixed = Path(env["sub"]).read_bytes()
    os.remove(worker.DB)
    rec = worker.process(env["video"], env["sub"])
    assert rec["status"] == "corrected" and rec["input_sha256"] == row(env["sub"])["input_sha256"]
    assert files_of(env["folder"]) == {Path(env["sub"]).name: fixed, kept_of(env).name: before}
    assert worker.process(env["video"], env["sub"])["status"] == "unchanged"


def test_lost_state_and_the_download_now_in_sync_gets_its_name_back(env):
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    fixed = Path(env["sub"]).read_bytes()
    os.remove(worker.DB)
    Path(env["video"]).write_bytes(b"another release")
    env["refs"][:] = [(ref_cues([[s + 10, e + 10, k] for s, e, k in env["base"]]), "#3 en English")]
    rec = worker.process(env["video"], env["sub"])
    assert rec["status"] == "in_sync" and Path(env["sub"]).read_bytes() == before
    baks = [p for p in env["folder"].iterdir() if p.suffix == ".bak"]
    assert [b.read_bytes() for b in baks] == [fixed] and rec["kept_aside"] == [str(baks[0])]   # not deleted


def test_lost_state_and_an_unknown_subtitle_is_kept_aside(env):
    """No record: whatever the subtitle holds, if it is not the new correction, it is kept."""
    before = write_late(env)
    worker.process(env["video"], env["sub"])
    os.remove(worker.DB)
    other = write_late(env, shift=-7)                                    # a new download, unknown to the state
    rec = worker.process(env["video"], env["sub"])
    assert rec["status"] == "corrected" and kept_of(env).read_bytes() == before
    assert [p.read_bytes() for p in env["folder"].glob("*.bak")] == [other]


def test_unknown_replaced_file_is_kept_aside_not_deleted(env):
    """A kept file the state does not account for (here: the subtitle is a download in
    sync) is renamed, never removed."""
    core.write(env["sub"], tgt_cues(env["base"]))
    worker.process(env["video"], env["sub"])
    kept_of(env).write_bytes(b"something else")
    rec = worker.process(env["video"], env["sub"])
    assert rec["status"] == "in_sync" and not kept_of(env).exists()
    assert [p.read_bytes() for p in env["folder"].glob("*.bak")] == [b"something else"]
    assert worker.process(env["video"], env["sub"])["status"] == "unchanged"


def test_old_replaced_file_of_a_known_download_is_replaced_by_the_new_download(env):
    write_late(env, shift=10)
    worker.process(env["video"], env["sub"])
    second = write_late(env, shift=-7)
    rec = worker.process(env["video"], env["sub"], origin="bazarr")
    assert rec["status"] == "corrected" and kept_of(env).read_bytes() == second
    assert not list(env["folder"].glob("*.bak"))


# --- an interrupted run is completed next time -------------------------------------------------

def _late(shift, text="cue k{k}"):
    def step(env, video, sub):
        env["refs"][:] = [(ref_cues(env["base"]), "#3 en English")]
        core.write(sub, tgt_cues(env["base"], warp=lambda t: t + shift, text=text))
    return step


def _new_video(shift):
    def step(env, video, sub):
        Path(video).write_bytes(f"another release {shift}".encode())
        env["refs"][:] = [(ref_cues([[s + shift, e + shift, k] for s, e, k in env["base"]]), "#3 en English")]
    return step


def _former(which, then=None):
    """The kept download is found under a former name: the one versions up to 0.12 gave it
    ("legacy"), or the other SEMSYNC_KEEP_DOWNLOAD value's ("other"), as state.db recorded it."""
    def step(env, video, sub):
        kept = worker.kept_path(sub)
        if os.path.exists(kept):
            old = (worker.tagged_path(video, sub, worker.REPLACED) if which == "legacy"
                   else worker.former_kept_paths(video, sub)[0])
            os.replace(kept, old)
            st = State(worker.DB)
            st.update(sub, replaced_path=old)
            st.close()
        if then:
            then(env, video, sub)
    return step


SCENARIOS = {
    "first_correction": [_late(10)],
    "new_download": [_late(10), _late(-7)],
    "video_changed": [_late(10), _new_video(3)],
    "back_in_sync": [_late(10), _new_video(10)],
    "new_download_in_sync": [_late(10), _late(0, text="new k{k}")],
    "legacy_name": [_late(10), _former("legacy")],
    "legacy_name_new_download": [_late(10), _former("legacy", _late(-7))],
    "legacy_name_back_in_sync": [_late(10), _former("legacy", _new_video(10))],
    "other_keep_mode": [_late(10), _former("other")],
}


def _pair(env, name):
    folder = env["tmp"] / name
    folder.mkdir()
    video = folder / "Show - S01E01.mkv"
    video.write_bytes(b"video")
    return folder, str(video), str(folder / "Show - S01E01.fr.srt")


@pytest.mark.parametrize("output", ["replace", "replace_hidden", "side"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_a_run_interrupted_at_any_step_loses_nothing(env, monkeypatch, scenario, output):
    """The process is killed after each file operation in turn (and before the decision is
    recorded); the next run must end with the files an uninterrupted run gives."""
    monkeypatch.setattr(worker, "OUTPUT", output.split("_")[0])
    monkeypatch.setattr(worker, "KEEP_DOWNLOAD", "hidden" if output.endswith("hidden") else "visible")
    steps = SCENARIOS[scenario]

    def play(name, crash_at=None, crash_put=False):
        folder, video, sub = _pair(env, name)
        for step in steps[:-1]:
            step(env, video, sub); worker.process(video, sub)
        steps[-1](env, video, sub)
        calls, real_replace, real_put, real_update = [0], os.replace, State.put, State.update

        def replace(a, b):
            calls[0] += 1
            if calls[0] == crash_at:
                raise KeyboardInterrupt("killed")
            return real_replace(a, b)

        def put(self, rec):
            if crash_put:
                raise KeyboardInterrupt("killed")
            return real_put(self, rec)

        def update(self, sub, **fields):
            if crash_put:
                raise KeyboardInterrupt("killed")
            return real_update(self, sub, **fields)
        with monkeypatch.context() as m:
            m.setattr(os, "replace", replace)
            m.setattr(State, "put", put)
            m.setattr(State, "update", update)
            try:
                first = worker.process(video, sub)["status"]
                crashed = False
            except KeyboardInterrupt:
                first, crashed = None, True
        if crashed:
            worker.process(video, sub)                               # the next run
        again = worker.process(video, sub)["status"]
        kept = row(sub)["replaced_path"]
        return crashed, (files_of(folder), kept and os.path.basename(kept)), again, first

    _, expected, again, first = play("reference")
    assert again == "unchanged" and not any(n.endswith((".bak", ".tmp")) for n in expected[0])
    n = 1
    while True:
        crashed, got, again, _ = play(f"crash{n}", crash_at=n)
        assert got == expected and again == "unchanged", f"killed at file operation {n}"
        if not crashed:
            break
        n += 1
    crashed, got, again, _ = play("crash_put", crash_put=True)
    # a run that records nothing (unchanged, nothing renamed) cannot be killed while recording
    assert (crashed or first == "unchanged") and got == expected and again == "unchanged"


def test_the_intent_is_recorded_before_any_file_changes(env, monkeypatch):
    write_late(env)
    seen = []
    real_begin = State.begin

    def begin(self, sub, intent):
        seen.append((intent, sorted(p.name for p in env["folder"].iterdir() if not p.name.startswith("."))))
        return real_begin(self, sub, intent)
    monkeypatch.setattr(State, "begin", begin)
    worker.process(env["video"], env["sub"])
    (intent, names), = seen
    assert names == ["Show - S01E01.fr.srt", "Show - S01E01.mkv"]          # nothing written yet
    assert intent["output_sha256"] == row(env["sub"])["output_sha256"] and intent["output_path"] == env["sub"]
    assert row(env["sub"])["pending"] is None


def test_a_subtitle_replaced_during_the_run_is_not_overwritten(env, monkeypatch):
    """The downloader writes a new subtitle while the worker aligns the previous one."""
    write_late(env)
    real = core.resync

    def resync(*a, **k):
        result = real(*a, **k)
        core.write(env["sub"], tgt_cues(env["base"], text="new k{k}"))
        return result
    monkeypatch.setattr(core, "resync", resync)
    rec = worker.process(env["video"], env["sub"])
    new = Path(env["sub"]).read_bytes()
    assert rec["status"] == worker.CHANGED and row(env["sub"]) is None
    assert sorted(p.name for p in env["folder"].iterdir()) == ["Show - S01E01.fr.srt", "Show - S01E01.mkv"]
    monkeypatch.setattr(core, "resync", real)
    assert worker.process(env["video"], env["sub"])["status"] == "in_sync"   # the new file is a new job
    assert Path(env["sub"]).read_bytes() == new


# --- files -------------------------------------------------------------------------------------

def test_a_failed_write_leaves_no_temporary_file(env, monkeypatch):
    write_late(env)

    def full(path, cues):
        raise OSError("disk full")
    monkeypatch.setattr(core, "write", full)
    with pytest.raises(OSError):
        worker.process(env["video"], env["sub"])
    assert sorted(p.name for p in env["folder"].iterdir()) == ["Show - S01E01.fr.srt", "Show - S01E01.mkv"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_written_files_keep_the_permissions_of_the_download(env):
    write_late(env)
    os.chmod(env["sub"], 0o640)
    worker.process(env["video"], env["sub"])
    assert os.stat(env["sub"]).st_mode & 0o777 == 0o640
    assert os.stat(kept_of(env)).st_mode & 0o777 == 0o640


# --- queue -------------------------------------------------------------------------------------

def write_job(name, job):
    os.makedirs(worker.QUEUE, exist_ok=True)
    path = Path(worker.QUEUE) / name
    path.write_text(job if isinstance(job, str) else json.dumps(job), encoding="utf-8")
    return path


def drain(stuck=None):
    st = State(worker.DB)
    try:
        return worker.drain(st, stuck)
    finally:
        st.close()


def test_drain_processes_jobs_and_moves_failed_ones(env, monkeypatch):
    write_late(env)
    write_job("1.job", {"video": env["video"], "sub": env["sub"]})
    write_job("2.job", "{not json")
    write_job("3.job", {"video": env["video"]})                          # no subtitle
    assert drain() == {"corrected": 1, "error": 2}
    assert os.listdir(worker.QUEUE) == [] and sorted(os.listdir(worker.FAILED)) == ["2.job", "3.job"]
    errors = [json.loads(x) for x in Path(worker.LOG).read_text(encoding="utf-8").splitlines()]
    errors = [e for e in errors if e["status"] == "error"]
    assert len(errors) == 2 and all("trace" in e for e in errors)          # the traceback: in the file


def test_error_trace_is_not_printed_on_stdout(env, capsys):
    write_job("2.job", "{not json")
    drain()
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "trace" not in json.loads(out[0])


def test_a_job_removed_meanwhile_is_skipped(env, monkeypatch):
    os.makedirs(worker.QUEUE, exist_ok=True)
    monkeypatch.setattr(worker, "queued_jobs", lambda: [str(Path(worker.QUEUE) / "gone.job")])
    assert drain() == {}
    assert not os.path.exists(worker.FAILED) or os.listdir(worker.FAILED) == []


def test_queued_jobs_survive_a_job_removed_between_listing_and_sorting(env, monkeypatch):
    write_job("a.job", {}); write_job("b.job", {})
    real = os.path.getmtime

    def getmtime(p):
        if p.endswith("a.job"):
            raise FileNotFoundError(p)
        return real(p)
    monkeypatch.setattr(os.path, "getmtime", getmtime)
    assert [os.path.basename(p) for p in worker.queued_jobs()] == ["b.job"]


def test_a_job_that_cannot_be_moved_is_not_taken_again(env, monkeypatch):
    job = write_job("stuck.job", "{not json")

    def denied(*a):
        raise PermissionError(a[0])
    stuck = set()
    with monkeypatch.context() as m:
        m.setattr(os, "replace", denied)
        m.setattr(os, "remove", denied)
        drain(stuck)
    assert stuck == {str(job)}
    calls = []
    monkeypatch.setattr(worker, "run_job", lambda j, s: calls.append(j))
    drain(stuck)
    assert calls == []


def test_jobs_outside_the_media_root_are_skipped(env, monkeypatch):
    write_late(env)
    monkeypatch.setattr(worker, "MEDIA_ROOT", str(env["tmp"] / "elsewhere"))
    write_job("1.job", {"video": env["video"], "sub": env["sub"]})
    assert drain() == {"skipped": 1}
    monkeypatch.setattr(worker, "MEDIA_ROOT", str(env["tmp"]))
    write_job("2.job", {"video": env["video"], "sub": env["sub"]})
    assert drain() == {"corrected": 1}


def test_backfill_never_overwrites_pending_jobs(env):
    core.write(env["sub"], tgt_cues(env["base"]))
    worker.backfill(str(env["tmp"] / "Show"))
    worker.backfill(str(env["tmp"] / "Show"))
    names = os.listdir(worker.QUEUE)
    assert len(names) == 2 and all(n.endswith(".job") for n in names)       # no .tmp left, none overwritten


def test_backfill_pairs_a_subtitle_with_the_longest_video_name(env):
    long_video = env["folder"] / "Show - S01E01.Extended.mkv"
    long_video.write_bytes(b"video")
    sub = env["folder"] / "Show - S01E01.Extended.fr.srt"
    core.write(str(sub), tgt_cues(env["base"]))
    worker.backfill(str(env["tmp"] / "Show"))
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in Path(worker.QUEUE).glob("*.job")]
    assert [(j["video"], j["sub"]) for j in jobs] == [(str(long_video), str(sub))]


def test_backfill_queues_a_title_that_contains_our_tag(env):
    (env["folder"] / "The.Replaced.2024.mkv").write_bytes(b"video")
    core.write(str(env["folder"] / "The.Replaced.2024.fr.srt"), tgt_cues(env["base"]))
    worker.backfill(str(env["tmp"] / "Show"))
    subs = [Path(json.loads(p.read_text(encoding="utf-8"))["sub"]).name for p in Path(worker.QUEUE).glob("*.job")]
    assert subs == ["The.Replaced.2024.fr.srt"]


# --- settings and command line -----------------------------------------------------------------

@pytest.mark.parametrize("attr,value,word", [
    ("OUTPUT", "sidee", "SEMSYNC_OUTPUT"), ("KEEP_DOWNLOAD", "invisible", "SEMSYNC_KEEP_DOWNLOAD"),
    ("EXTRA_LINES", "remove", "SEMSYNC_EXTRA_LINES"),
    ("LOG_MAX_BYTES", None, "SEMSYNC_LOG_MAX_MB"), ("MEDIA_ROOT", "/no/such/folder", "SEMSYNC_MEDIA_ROOT"),
])
@pytest.mark.parametrize("cmd", [["one", "v.mkv", "v.fr.srt"], ["backfill", "/nowhere"], ["run"]])
def test_wrong_settings_stop_before_any_job(env, monkeypatch, attr, value, word, cmd):
    monkeypatch.delattr(os, "nice", raising=False)
    monkeypatch.setattr(worker, attr, value)
    monkeypatch.setattr(worker, "run", lambda: pytest.fail("started"))
    with pytest.raises(SystemExit) as e:
        worker.main(cmd)
    assert word in str(e.value.code)


def test_wrong_default_model_stops_before_any_job(env, monkeypatch):
    monkeypatch.delattr(os, "nice", raising=False)
    monkeypatch.setattr(core, "DEFAULT_MODEL", "bert")
    with pytest.raises(SystemExit) as e:
        worker.main(["one", env["video"], env["sub"]])
    assert "SEMSYNC_MODEL" in str(e.value.code)


@pytest.mark.parametrize("argv", [
    ["one", "v.mkv"], ["one", "--model", "--force", "v.mkv", "v.fr.srt"], ["one", "--model", "bert", "v", "s"],
    ["status", "--feilds"], ["history"], ["frobnicate"],
])
def test_wrong_command_lines_are_refused_with_usage(monkeypatch, capsys, argv):
    monkeypatch.delattr(os, "nice", raising=False)
    with pytest.raises(SystemExit) as e:
        worker.main(argv)
    assert e.value.code == 2 and "usage" in capsys.readouterr().err


def test_one_takes_its_options_before_or_after_the_paths(env, monkeypatch, capsys):
    monkeypatch.delattr(os, "nice", raising=False)
    monkeypatch.setattr(worker, "BASE", str(env["tmp"]))
    write_late(env)
    worker.main(["one", "--force", "--model", "minilm", env["video"], env["sub"]])
    assert row(env["sub"])["model_choice"] == "minilm"
    worker.main(["one", env["video"], env["sub"], "--model", "default"])
    assert row(env["sub"])["model_choice"] is None
    worker.main(["one", env["video"], env["sub"]])
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rec["status"] == "unchanged" and rec["last"] == "corrected"    # what the Jellyfin plugin reads


def test_status_lists_a_run_interrupted_before_its_first_decision(env, capsys):
    st = State(worker.DB)
    try:
        st.begin(env["sub"], {"input_sha256": "x", "output_sha256": None, "output_path": None})
    finally:
        st.close()
    worker.status([])
    assert '"pending": 1' in capsys.readouterr().out
    worker.status([], fields=True)
    assert "pending" in capsys.readouterr().out
