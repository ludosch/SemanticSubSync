"""Bench on real downloaded subtitles, which have no ground truth.

The corpus is a folder with one sub-folder per episode and an `index.json` listing its files:
[{"path": "show/S02E03/fr_xxx.srt", "lang": "fr"}, ...]. Subtitles are copyrighted: keep the corpus
out of the repository. In each episode the first English file is the reference; every other file
is aligned on it, then the reference on it (both directions).

Measured for each pair:
- the decision (corrected / in_sync / unsure) and the lines dropped (scenes the reference lacks);
- invariants that hold whatever the truth: output in time order, nothing before 0 s, no overlap
  created by the correction;
- a proxy accuracy: target lines with ONE obvious translation in the reference (cosine >= 0.70,
  0.15 above any other reference line, 12+ characters) should start within 0.5 s / 1 s of it.
  Two translations are rarely cut identically, so even a subtitle in sync does not reach 100 %.

Usage: SEMSYNC_CACHE=... [SEMSYNC_MODEL=...] [SEMSYNC_MODEL_DIR=...] uv run --extra model python tools/bench_real.py CORPUS [OUT.jsonl]
"""
import json, os, statistics, sys
from collections import Counter

import numpy as np

from semantic_subsync import core, media


def confident_pairs(tgt, ref):
    """{target index: reference index} for target lines with one obvious translation."""
    ti, ri, s = core.similarity(tgt, ref)          # the engine's cue-to-cue cosine matrix
    tt = core.embeddable(tgt)[1]
    o = np.argsort(-s, axis=1)
    return {i: ri[o[a, 0]] for a, i in enumerate(ti)
            if s[a, o[a, 0]] >= 0.70 and s[a, o[a, 0]] - s[a, o[a, 1]] >= 0.15 and len(tt[i]) >= 12}


def proxy_accuracy(final, tgt, ref, pairs, tol):
    """Share of confident lines that start within `tol` s of their translation. Lines are found
    in the output by their text (only texts unique in the file); a dropped line counts as wrong."""
    count = Counter(c[2] for c in tgt)
    where = {c[2]: c[0] for c in final if count[c[2]] == 1}
    ok = [i for i in pairs if count[tgt[i][2]] == 1]
    return sum(tgt[i][2] in where and abs(where[tgt[i][2]] - ref[pairs[i]][0]) <= tol for i in ok) / max(1, len(ok))


def invariants(out, tgt, tol=0.3):
    """Violations: lines out of time order, lines before 0 s, overlaps longer than `tol` s that
    the input did not have."""
    own = sum(a[1] > b[0] + tol for a, b in zip(tgt, tgt[1:]))
    return {"unordered": sum(a[0] > b[0] for a, b in zip(out, out[1:])),
            "before_zero": sum(c[0] < 0 for c in out),
            "overlaps_created": max(0, sum(a[1] > b[0] + tol for a, b in zip(out, out[1:])) - own)}


def pairs(corpus):
    idx = json.load(open(os.path.join(corpus, "index.json"), encoding="utf-8"))
    for ep in sorted({os.path.dirname(x["path"]) for x in idx}):
        files = [x for x in idx if os.path.dirname(x["path"]) == ep]
        refs = [x for x in files if x["lang"] == "en"]
        for x in files if refs else []:
            if x is not refs[0]:
                yield ep, x, refs[0]
                yield ep, refs[0], x


def measure(corpus, ep, t, r):
    T = media.read_srt(os.path.join(corpus, t["path"]), t["lang"])
    R = media.read_srt(os.path.join(corpus, r["path"]), r["lang"])
    status, out, st = core.resync(T, R)
    rec = {"episode": ep, "tgt": t["path"], "tgt_lang": t["lang"], "ref": r["path"], "ref_lang": r["lang"],
           "status": status, "why": st.get("status"), "coverage": st.get("coverage"),
           "segments": st.get("segments"), "dropped": st.get("dropped"),
           "max_abs_offset": st.get("max_abs_offset"), "drift_ppm": (st.get("seg") or [{}])[0].get("drift_ppm")}
    raw, _ = core.sync(T, R)
    if raw is not None:
        rec.update(invariants(raw, T))
    if status != "unsure":
        p = confident_pairs(T, R)
        final = out if status == "corrected" else T
        rec["n_confident"] = len(p)
        rec["before"] = [round(proxy_accuracy(T, T, R, p, k), 3) for k in (0.5, 1.0)]
        rec["after"] = [round(proxy_accuracy(final, T, R, p, k), 3) for k in (0.5, 1.0)]
    return rec


def summary(rs):
    med = lambda xs: round(statistics.median(xs), 3) if xs else None
    c = [r for r in rs if r["status"] == "corrected"]; i = [r for r in rs if r["status"] == "in_sync"]
    return {"pairs": len(rs), "episodes": len({r["episode"] for r in rs}),
            "languages": sorted({r["tgt_lang"] for r in rs} | {r["ref_lang"] for r in rs}),
            "status": dict(Counter(r["status"] for r in rs)),
            "in_sync_proxy_median": [med([r["before"][k] for r in i]) for k in (0, 1)],
            "corrected_before_median": [med([r["before"][k] for r in c]) for k in (0, 1)],
            "corrected_after_median": [med([r["after"][k] for r in c]) for k in (0, 1)],
            "corrected_after_1s_ge_0.9": sum(r["after"][1] >= 0.9 for r in c),
            "worse_after": sum(r["after"][1] < r["before"][1] for r in c),
            "dropped_lines": sum(r["dropped"] or 0 for r in rs),
            "invariant_violations": sum(bool(r.get("unordered") or r.get("before_zero") or r.get("overlaps_created")) for r in rs)}


def main():
    corpus = sys.argv[1]
    out = open(sys.argv[2] if len(sys.argv) > 2 else "bench_real.jsonl", "w", encoding="utf-8")
    rs = []
    for ep, t, r in pairs(corpus):
        rec = measure(corpus, ep, t, r); rs.append(rec)
        out.write(json.dumps(rec, default=lambda o: o.item(), ensure_ascii=False) + "\n"); out.flush()
        print(ep, rec["tgt_lang"], "<-", rec["ref_lang"], rec["status"], rec.get("before"), "->", rec.get("after"), flush=True)
    print(json.dumps(summary(rs), indent=1, default=lambda o: o.item()))


if __name__ == "__main__":
    main()
