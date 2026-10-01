"""semantic-subsync: re-time one subtitle file on a reference subtitle or on a video's embedded one."""
import argparse, json, os, sys

from . import __version__, core, media

EXIT_OK, EXIT_REFUSED, EXIT_ERROR = 0, 1, 2


def default_output(subtitle):
    """'Movie.fr.srt' -> 'Movie.fr.synced.srt'."""
    return os.path.splitext(subtitle)[0] + ".synced.srt"


def parser():
    ap = argparse.ArgumentParser(
        prog="semantic-subsync",
        description="Re-time SUBTITLE on REFERENCE by matching lines on meaning, across languages. "
                    "REFERENCE is a subtitle file already in sync with the video, or the video itself: "
                    "its fullest embedded text subtitle (any language) is then used.",
        epilog="Exit status: 0 corrected or already in sync, 1 refused (reference not trustworthy "
               "for this subtitle), 2 error.")
    ap.add_argument("subtitle", help="the .srt to re-time (never modified)")
    ap.add_argument("reference", help="a .srt in sync with the video, or the video file")
    ap.add_argument("-o", "--output", help="where to write the result (default: SUBTITLE.synced.srt)")
    ap.add_argument("--track", type=int, metavar="INDEX",
                    help="with a video reference: use this stream index (see ffprobe) instead of the fullest track")
    ap.add_argument("--lang", metavar="CODE",
                    help="language of SUBTITLE (e.g. fr, ru), to read files that are not UTF-8 "
                         "(default: the tag in its name, as in Movie.ru.srt)")
    ap.add_argument("--min-coverage", type=float, default=core.MIN_COVERAGE, metavar="X",
                    help=f"refuse below this share of anchored lines (default {core.MIN_COVERAGE})")
    ap.add_argument("--json", action="store_true", help="print the decision and statistics as JSON")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    out_path = a.output or default_output(a.subtitle)
    if os.path.abspath(out_path) == os.path.abspath(a.subtitle):
        print("semantic-subsync: the output would overwrite the input subtitle", file=sys.stderr)
        return EXIT_ERROR
    for p in (a.subtitle, a.reference):
        if not os.path.isfile(p):
            print(f"semantic-subsync: no such file: {p}", file=sys.stderr)
            return EXIT_ERROR

    if a.reference.lower().endswith(".srt"):
        ref, ref_desc = media.read_srt(a.reference), os.path.basename(a.reference)
    else:
        found = media.best_reference(a.reference, {a.track} if a.track is not None else None)
        if found is None:
            print("semantic-subsync: no usable embedded text subtitle in the video "
                  "(bitmap tracks such as PGS cannot be read)", file=sys.stderr)
            return EXIT_ERROR
        ref, ref_desc = found

    status, cues, st = core.resync(media.read_srt(a.subtitle, a.lang), ref, min_coverage=a.min_coverage)
    if cues is not None:
        core.write(out_path, cues)
    if a.json:
        print(json.dumps({**st, "engine_status": st.get("status"), "status": status,
                          "output": out_path if cues is not None else None, "reference": ref_desc},
                         ensure_ascii=False))
    else:
        if status == "corrected":
            msg = (f"corrected -> {out_path} ({st['segments']} segment(s), "
                   f"largest shift {st['max_abs_offset']:+.2f} s)")
        elif status == "in_sync":
            msg = "already in sync, nothing written"
        else:
            msg = (f"refused ({st.get('status')}, coverage {st.get('coverage', 0)}): "
                   "the reference does not match this subtitle")
        print(f"{msg} [reference: {ref_desc}]", file=sys.stderr)
    return EXIT_REFUSED if status == "refused" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
