"""Reading subtitle files and the text subtitles embedded in a video (needs ffmpeg / ffprobe)."""
import json, os, subprocess, tempfile

from . import core

TEXT_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}     # bitmap (PGS, VobSub) cannot be read
VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm")
MIN_REF_CUES = 20          # fewer cues: a forced/signs track or a partial one, not a usable reference


def read_srt(path):
    """core.parse, but downloaded subtitles are sometimes cp1252 rather than UTF-8."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return core.parse_text(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return core.parse_text(raw.decode("utf-8", errors="replace"))


def embedded_references(video, workdir, streams=None):
    """Extract the non-forced embedded text subtitles in ONE read of the file; return [(cues, desc)].
    `streams`: restrict to these ffprobe stream indexes (forced tracks are then allowed too)."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=index,codec_name,codec_type:stream_tags=language,title:stream_disposition=forced",
                            "-of", "json", video], capture_output=True, text=True, timeout=300)
    found = json.loads(probe.stdout or "{}").get("streams", [])
    subs = [s for s in found if s.get("codec_type") == "subtitle" and s.get("codec_name") in TEXT_CODECS
            and (s["index"] in streams if streams else not s.get("disposition", {}).get("forced"))]
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
            tags = s.get("tags", {})
            out.append((core.parse(p), f"#{s['index']} {tags.get('language', 'und')} {tags.get('title', '')}".strip()))
    return out


def best_reference(video, streams=None):
    """The fullest embedded text subtitle of `video` as (cues, desc), or None.
    Any language will do: an embedded track is assumed to be in sync with its video."""
    with tempfile.TemporaryDirectory() as wd:
        refs = [r for r in embedded_references(video, wd, streams) if len(r[0]) >= MIN_REF_CUES]
    return max(refs, key=lambda r: len(r[0])) if refs else None
