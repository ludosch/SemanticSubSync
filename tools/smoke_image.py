"""Smoke test of a built image (run by release.yml): ffmpeg can mux a video with an embedded text
subtitle, and the worker reads its language, its hearing-impaired flag and its lines back.

    docker run --rm -v "$PWD/tools:/tools:ro" IMAGE python /tools/smoke_image.py
"""
import os, subprocess, tempfile

from semantic_subsync import media

with tempfile.TemporaryDirectory() as d:
    srt, mkv = os.path.join(d, "a.srt"), os.path.join(d, "v.mkv")
    with open(srt, "w", encoding="utf-8") as f:
        f.write("".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},800\nLine {i}\n\n" for i in range(1, 31)))
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=64x64:duration=32", "-i", srt,
                    "-map", "0", "-map", "1", "-c:v", "mpeg4", "-c:s", "srt", "-metadata:s:s:0", "language=eng",
                    "-disposition:s:0", "hearing_impaired", mkv], check=True)
    tracks = media.probe_subtitles(mkv)
    print(tracks)
    assert [(t["lang"], t["kind"], t["text"]) for t in tracks] == [("en", "hi", True)], tracks
    cues, desc = media.best_reference(mkv, tracks=tracks)
    print(desc, len(cues), cues[0])
    assert len(cues) == 30 and cues[0][2] == "Line 1"
print("ok")
