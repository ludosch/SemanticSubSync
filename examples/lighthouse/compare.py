"""Run SemanticSubSync and the other tools found on PATH on the three situations; print the scores.

Tools looked up: alass-cli (or alass), ffsubsync, lapse. Missing ones are skipped. Outputs are
written as <situation>/out.<tool>.srt.

Score: share of lines that start within 300 ms of their true position (truth.fr.srt). Lines that
have no place in the video (situation 2) are not scored. For the wrong reference (situation 3),
the only good answer is to leave the file alone.

Usage: uv run --extra model python examples/lighthouse/compare.py
"""
import glob, json, os, shutil, subprocess

from semantic_subsync import core, media

HERE = os.path.dirname(os.path.abspath(__file__))


def tools():
    alass = shutil.which("alass-cli") or shutil.which("alass")
    ffs, lapse = shutil.which("ffsubsync"), shutil.which("lapse")
    found = {}
    if alass:
        found["alass"] = lambda ref, sub, out: [alass, ref, sub, out]
    if ffs:
        found["ffsubsync"] = lambda ref, sub, out: [ffs, ref, "-i", sub, "-o", out]
        found["ffsubsync --split-penalty 5"] = lambda ref, sub, out: [ffs, ref, "-i", sub, "-o", out,
                                                                      "--split-penalty", "5"]
    if lapse:
        found["LAPSE"] = lambda ref, sub, out: [lapse, ref, sub, "--output", out, "--force", "--no-backup",
                                                "--no-cache", "--json", "--quiet"]
    return found


def accuracy(path, truth_path):
    """(share of scored lines within 300 ms of their true start, worst error in s). Lines are
    found in the truth by their text; a file with no line in the truth scores (0.0, inf)."""
    truth = {}
    for s, _, x in media.read_srt(truth_path):
        truth.setdefault(x, []).append(s)
    err = [abs(s - truth[x].pop(0)) for s, _, x in media.read_srt(path) if truth.get(x)]
    if not err:
        return 0.0, float("inf")
    return sum(e <= 0.3 for e in err) / len(err), max(err)


def score(path, truth_path, downloaded):
    if not os.path.exists(truth_path):
        out = media.read_srt(path)
        moved = max((abs(a[0] - b[0]) for a, b in zip(out, downloaded)), default=0.0)
        return f"changed the file (lines moved by up to {moved:.0f} s)" if moved > 0.01 else "left unchanged"
    share, worst = accuracy(path, truth_path)
    return f"{100 * share:5.1f} % within 300 ms, worst {worst:5.1f} s"


def main():
    for case in sorted(glob.glob(os.path.join(HERE, "[0-9]-*"))):
        ref = glob.glob(os.path.join(case, "reference.*.srt"))[0]
        sub, truth = os.path.join(case, "downloaded.fr.srt"), os.path.join(case, "truth.fr.srt")
        downloaded = media.read_srt(sub)
        print(f"\n{os.path.basename(case)}")
        if os.path.exists(truth):
            print(f"  {'before':28s} {score(sub, truth, downloaded)}")
        for name, cmd in tools().items():
            out = os.path.join(case, f"out.{name.split()[0]}{'-split' if 'split' in name else ''}.srt")
            p = subprocess.run(cmd(ref, sub, out), capture_output=True, text=True)
            note = ""
            if name == "LAPSE" and p.stdout.strip():
                note = f"  (LAPSE verdict: {json.loads(p.stdout.strip().splitlines()[-1]).get('verdict')})"
            res = score(out, truth, downloaded) if os.path.exists(out) else "no output"
            print(f"  {name:28s} {res}{note}")
        status, cues, st = core.resync(downloaded, media.read_srt(ref))
        if cues is not None:
            out = os.path.join(case, "out.semantic-subsync.srt")
            core.write(out, cues)
            status = score(out, truth, downloaded)
        print(f"  {'SemanticSubSync':28s} {status}")


if __name__ == "__main__":
    main()
