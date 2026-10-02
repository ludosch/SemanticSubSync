"""Reading subtitle files and the text subtitles embedded in a video (needs ffmpeg / ffprobe)."""
import json, os, re, subprocess, tempfile

from . import core

TEXT_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}     # bitmap (PGS, VobSub) cannot be read
VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm")
MIN_REF_CUES = 20          # fewer cues: a forced/signs track or a partial one, not a usable reference


# Legacy Windows code page of each language, for subtitles that are not UTF-8 (most older
# downloads). A single-byte code page decodes ANY bytes without error, so guessing one blindly
# turns Cyrillic or Greek into Latin gibberish: the subtitle's language decides instead.
_CODEPAGES = {
    "cp1252": "en eng fr fre fra es spa pt por pt-br pob br it ita de ger deu nl dut nld sv swe "
              "da dan no nor nb nob fi fin ca cat gl glg id ind ms may msa eu baq eus",
    "cp1250": "pl pol cs cze ces cz sk slo slk hu hun ro rum ron hr hrv sl slv bs bos sq alb sqi",
    "cp1251": "ru rus uk ukr ua bg bul mk mac mkd be bel",
    "cp1253": "el gre ell gr",
    "cp1254": "tr tur az aze",
    "cp1255": "he heb",
    "cp1256": "ar ara fa per fas ur urd",
    "cp1257": "lt lit lv lav et est",
    "cp1258": "vi vie",
    "cp874": "th tha",
    "cp932": "ja jpn jp",
    "cp949": "ko kor",
}
CODEPAGE = {lang: cp for cp, langs in _CODEPAGES.items() for lang in langs.split()}


def language_of(path):
    """Language tag of a subtitle named like most tools do: 'Movie.fr.srt', 'Show.S01E01.pt-BR.hi.srt'."""
    for tok in reversed(os.path.basename(path).lower().split(".")[1:-1]):
        if (tok in CODEPAGE or tok in LANGUAGE_KEY) and tok not in HI_TAGS:   # "hi": hearing impaired, not Hindi
            return tok
    return None


def decode(raw, lang=None):
    """Text of a subtitle file: UTF-8/UTF-16 when it is, else the code page of `lang`, else cp1252
    unless it reads as gibberish (another script), else a guess (charset-normalizer)."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if lang in CODEPAGE:
        return raw.decode(CODEPAGE[lang], errors="replace")
    # Western text read as cp1252 has a few accented letters (0-11 % measured on real files);
    # Cyrillic, Greek or Arabic read as cp1252 is almost only accented letters ("Ïðèâåò", 94-99 %),
    # Chinese ~40 %. Statistical guessing is unreliable on short Western files, so it only gets
    # the second case.
    western = raw.decode("cp1252", errors="replace")
    letters = [c for c in western if c.isalpha()]
    if sum("À" <= c <= "ÿ" for c in letters) <= 0.25 * len(letters):
        return western
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best is not None:
            return str(best)
    except ImportError:
        pass
    return raw.decode("cp1252", errors="replace")


def read_srt(path, lang=None):
    """core.parse for any encoding. `lang` (e.g. 'fr') defaults to the tag in the file name."""
    return core.parse_text(decode(open(path, "rb").read(), lang or language_of(path)))


# Embedded tracks are tagged ISO 639-2 ("fre", "ger"), file names mostly ISO 639-1 ("fr", "de"):
# both are brought to one key to tell whether a downloaded subtitle duplicates an embedded one.
_SAME_LANGUAGE = ("en eng|fr fre fra|es spa|de ger deu|it ita|pt por|pt-br pob br|nl dut nld|sv swe|"
                  "da dan|no nor nb nob|fi fin|pl pol|cs cze ces cz|sk slo slk|hu hun|ro rum ron|hr hrv|"
                  "sl slv|sr srp|bs bos|bg bul|mk mac mkd|ru rus|uk ukr ua|be bel|el gre ell gr|tr tur|"
                  "he heb|ar ara|fa per fas|hi hin|th tha|vi vie|id ind|ms may msa|ja jpn jp|ko kor|"
                  "zh chi zho cn zh-cn zh-tw|ca cat|eu baq eus|gl glg|et est|lv lav|lt lit|is ice isl")
LANGUAGE_KEY = {tag: g.split()[0] for g in _SAME_LANGUAGE.split("|") for tag in g.split()}
HI_TAGS = {"hi", "sdh", "cc"}
_HI_TITLE = re.compile(r"\b(sdh|hi|cc|hoh|deaf|hearing|malentendants?|sourds?)\b", re.I)


def language_key(tag):
    """'fre', 'fra', 'fr' -> 'fr'; unknown tags are only lower-cased."""
    tag = (tag or "").lower()
    return LANGUAGE_KEY.get(tag, tag)


def kind_of(path):
    """'normal', 'hi' (hearing impaired) or 'forced', from a subtitle file name ('Movie.fr.hi.srt')."""
    toks = set(os.path.basename(path).lower().split(".")[1:-1])
    return "forced" if "forced" in toks else "hi" if toks & HI_TAGS else "normal"


def probe_subtitles(video):
    """The subtitle tracks of `video`, as dicts: index, codec, text (readable as text), lang (a
    language_key), kind ('normal' / 'hi' / 'forced', from the disposition flags or the title), title."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
                            "stream=index,codec_name:stream_tags=language,title"
                            ":stream_disposition=forced,hearing_impaired",
                            "-of", "json", video], capture_output=True, text=True, timeout=300)
    out = []
    for s in json.loads(probe.stdout or "{}").get("streams", []):
        tags, disp = s.get("tags", {}), s.get("disposition", {})
        title = tags.get("title", "")
        kind = ("forced" if disp.get("forced") else
                "hi" if disp.get("hearing_impaired") or _HI_TITLE.search(title) else "normal")
        out.append({"index": s["index"], "codec": s.get("codec_name"), "text": s.get("codec_name") in TEXT_CODECS,
                    "lang": language_key(tags.get("language")), "kind": kind, "title": title})
    return out


def embedded_twin(video_tracks, sub):
    """The embedded text track that makes the downloaded subtitle `sub` redundant (same language
    and same kind), or None. Image tracks (PGS, VobSub) do not count: many players cannot show
    them without converting the video, so a text subtitle of the same language is still useful."""
    lang = language_key(language_of(sub))
    if not lang:
        return None
    kind = kind_of(sub)
    return next((t for t in video_tracks if t["text"] and t["lang"] == lang and t["kind"] == kind), None)


def embedded_references(video, workdir, streams=None, tracks=None):
    """Extract the non-forced embedded text subtitles in ONE read of the file; return [(cues, desc)].
    `streams`: restrict to these ffprobe stream indexes (forced tracks are then allowed too).
    `tracks`: the result of probe_subtitles(video), when already known."""
    found = probe_subtitles(video) if tracks is None else tracks
    subs = [s for s in found if s["text"] and (s["index"] in streams if streams else s["kind"] != "forced")]
    if not subs:
        return []
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video]
    for s in subs:
        cmd += ["-map", f"0:{s['index']}", "-f", "srt", os.path.join(workdir, f"{s['index']}.srt")]
    subprocess.run(cmd, capture_output=True, timeout=1800)
    out = []
    for s in subs:
        p = os.path.join(workdir, f"{s['index']}.srt")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            out.append((read_srt(p), f"#{s['index']} {s['lang'] or 'und'} {s['title']}".strip()))
    return out


def best_reference(video, streams=None, tracks=None):
    """The fullest embedded text subtitle of `video` as (cues, desc), or None.
    Any language will do: an embedded track is assumed to be in sync with its video."""
    with tempfile.TemporaryDirectory() as wd:
        refs = [r for r in embedded_references(video, wd, streams, tracks) if len(r[0]) >= MIN_REF_CUES]
    return max(refs, key=lambda r: len(r[0])) if refs else None
