"""semantic-subsync: re-time one subtitle file on a reference subtitle or on a video's embedded one."""
import argparse, json, os, sys

from . import __version__, core, media

EXIT_OK, EXIT_UNSURE, EXIT_ERROR = 0, 1, 2


def default_output(subtitle):
    """'Movie.fr.srt' -> 'Movie.fr.synced.srt'."""
    return os.path.splitext(subtitle)[0] + ".synced.srt"


def parser():
    ap = argparse.ArgumentParser(
        prog="semantic-subsync",
        description="Re-time SUBTITLE on REFERENCE by matching lines on meaning, across languages. "
                    "REFERENCE is a subtitle file already in sync with the video, or the video itself: "
                    "its fullest embedded text subtitle (any language) is then used.",
        epilog="Exit status: 0 corrected or already in sync, 1 unsure (too few lines match the reference: "
               "nothing written), 2 error.")
    ap.add_argument("subtitle", help="the .srt to re-time (never modified)")
    ap.add_argument("reference", help="a .srt in sync with the video, or the video file")
    ap.add_argument("-o", "--output", help="where to write the result (default: SUBTITLE.synced.srt)")
    ap.add_argument("--track", type=int, metavar="INDEX",
                    help="with a video reference: use this stream index (see ffprobe) instead of the fullest track")
    ap.add_argument("--lang", metavar="CODE", type=str.lower,
                    help="language of SUBTITLE (e.g. fr, ru), to read files that are not UTF-8 "
                         "(default: the tag in its name, as in Movie.ru.srt)")
    ap.add_argument("--extra-lines", choices=core.CHOICES["extra_lines"], default=core.P["extra_lines"],
                    help="lines the video has no room for (a credit, a recap or a scene it lacks): "
                         "drop them all (default), or keep them where nothing is shown nor said, "
                         "a block of consecutive lines whole or not at all")
    ap.add_argument("--model", choices=list(core.MODELS), default=core.DEFAULT_MODEL,
                    help=f"sentence model (default {core.DEFAULT_MODEL}, or $SEMSYNC_MODEL): static is much faster, "
                         "minilm is slower and a little better on some hard cases")
    ap.add_argument("--min-coverage", type=float, default=core.MIN_COVERAGE, metavar="X",
                    help=f"leave the file alone below this share of anchored lines (default {core.MIN_COVERAGE})")
    ap.add_argument("--json", action="store_true", help="print the decision and statistics as JSON")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


SUBTITLE_EXT = (".ass", ".ssa", ".vtt", ".sub", ".sup", ".idx", ".smi", ".ttml", ".dfxp")


class Refused(Exception):
    """A request the command line cannot honour: one line on stderr, exit status 2."""


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        return run(a)
    except Exception as e:   # ffmpeg missing or failing, model download, unreadable file...: one line, status 2
        msg = " ".join(str(e).split()) or type(e).__name__
        print(f"semantic-subsync: {msg}" if isinstance(e, Refused) else f"semantic-subsync: error: {msg}",
              file=sys.stderr)
        return EXIT_ERROR


def read_subtitle(path, what, lang=None):
    """The cues of an SRT file; Refused when it is another subtitle format or has no cue."""
    ext = os.path.splitext(path)[1].lower()
    if ext in SUBTITLE_EXT:
        raise Refused(f"{what} must be an SRT subtitle, not {ext} (convert it first): {path}")
    cues = media.read_srt(path, lang)
    if not cues:
        raise Refused(f"no SRT cue in {what}: {path}")
    return cues


def reference_of(a):
    """(cues, description) of the reference: an SRT file, or a text subtitle embedded in a video."""
    ext = os.path.splitext(a.reference)[1].lower()
    if ext == ".srt" or ext in SUBTITLE_EXT:
        if a.track is not None:
            raise Refused("--track only applies to a video reference")
        return read_subtitle(a.reference, "REFERENCE"), os.path.basename(a.reference)
    if a.track is None:
        found = media.best_reference(a.reference)
        if found is None:
            raise Refused("no usable embedded text subtitle in the video "
                          "(bitmap tracks such as PGS cannot be read)")
        return found
    found = media.best_reference(a.reference, {a.track}, min_cues=1)
    if found is None:
        raise Refused(f"stream #{a.track} of the video is not a text subtitle with lines (see ffprobe)")
    if len(found[0]) < core.MIN_CUES:
        raise Refused(f"stream #{a.track} has only {len(found[0])} lines (a forced or signs track?): "
                      f"at least {core.MIN_CUES} are needed to align on it")
    return found


def run(a):
    out_path = a.output or default_output(a.subtitle)
    for p, what in ((a.subtitle, "input subtitle"), (a.reference, "reference")):
        if os.path.abspath(out_path) == os.path.abspath(p):
            raise Refused(f"the output would overwrite the {what}")
    for p in (a.subtitle, a.reference):
        if not os.path.isfile(p):
            raise Refused(f"no such file: {p}")
    try:
        core.check_model(a.model)
    except ValueError as e:
        raise Refused(f"{e} (--model or $SEMSYNC_MODEL)") from None
    tgt = read_subtitle(a.subtitle, "SUBTITLE", a.lang)
    ref, ref_desc = reference_of(a)

    status, cues, st = core.resync(tgt, ref, p={"extra_lines": a.extra_lines},
                                   min_coverage=a.min_coverage, model=a.model)
    if cues is not None:
        core.write(out_path, cues)
    if a.json:
        print(json.dumps({**st, "engine_status": st.get("status"), "status": status,
                          "output": out_path if cues is not None else None, "reference": ref_desc, "model": a.model},
                         ensure_ascii=False))
    else:
        if status == "corrected":
            msg = (f"corrected -> {out_path} ({st['segments']} segment(s), "
                   f"largest shift {st['max_abs_offset']:+.2f} s)")
        elif status == "in_sync":
            msg = "already in sync, nothing written"
        elif "coverage" in st:
            msg = (f"unsure (coverage {st['coverage']}): "
                   "too few lines match the reference, nothing written")
        else:
            msg = (f"unsure ({st.get('status')}): too few lines can be matched "
                   f"(at least {core.MIN_CUES} with text on each side), nothing written")
        print(f"{msg} [reference: {ref_desc}]", file=sys.stderr)
    return EXIT_UNSURE if status == "unsure" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
