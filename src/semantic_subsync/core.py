#!/usr/bin/env python3
"""Semantic subtitle sync: re-time a subtitle (e.g. FR) on a reference in another
language (e.g. the original EN) by matching cues on MEANING, not on timing shape.

1. clean + embed every cue with a multilingual sentence model (local, deterministic)
2. candidate pairs: for each target cue, top-k reference cues (single or 2-cue merge)
3. weighted longest increasing chain -> monotonic anchors
4. drop anchors whose offset disagrees with their neighbours (robust local median)
5. every target cue gets the smoothed offset of its anchor neighbourhood

Usage: semantic-subsync target.srt reference.srt output.srt
"""
import json, re, sys, bisect
import numpy as np

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
P = dict(topk=3, min_sim=0.55, nb=7, max_dev=1.0, max_offset=900.0,
         merge="mean",   # "embed": encode 2-cue merges; "mean": average the two cue vectors (free)
         stride=1,       # embed every Nth target cue only (anchors); all cues are still re-timed
         min_seg=60.0,   # s: a segment shorter than this is a mis-match, not a cut -> merged into its neighbour
         deadband=0.5)   # s: a single constant correction smaller than this = natural FR/VO bias -> leave file untouched
# Drift only comes from frame-rate conversions, i.e. a handful of ratios for the whole file.
FPS = [23.976, 24.0, 25.0, 29.97, 30.0]
DRIFTS = sorted({round(a / b - 1, 6) for a in FPS for b in FPS if a / b - 1 and abs(a / b - 1) < 0.3} | {0.0})

def best_drift(at, ao, tol=0.5, lag=60.0):
    """Pick the ratio that makes anchor offsets flattest. Pairs are ~`lag` s apart so that a
    4 % drift (2.4 s per minute) is unmistakable even with sparse anchors; cuts only spoil
    the few pairs straddling them."""
    j = np.searchsorted(at, at + lag)
    ok = j < len(at); i, j = np.nonzero(ok)[0], j[ok]
    def score(b):
        d = np.abs((ao[j] - b * at[j]) - (ao[i] - b * at[i])); return int(np.sum(d < tol))
    scores = {b: score(b) for b in DRIFTS}
    best = max(scores.values())
    # prefer no drift unless a ratio is clearly better (deterministic tie-break)
    if scores[0.0] >= 0.95 * best: return 0.0
    return max(DRIFTS, key=lambda b: (scores[b], -abs(b)))

_model = None; _cache = {}
def embed(texts):
    global _model
    import hashlib, os
    key = hashlib.md5((MODEL + os.environ.get("SEMSYNC_MODEL_DIR", "") + "\x00" + "\x00".join(texts)).encode()).hexdigest()
    if key in _cache: return _cache[key]
    disk = os.environ.get("SEMSYNC_CACHE")
    if disk and os.path.exists(f"{disk}/{key}.npy"):
        _cache[key] = np.load(f"{disk}/{key}.npy"); return _cache[key]
    if _model is None:
        from fastembed import TextEmbedding
        d = os.environ.get("SEMSYNC_MODEL_DIR")   # e.g. an int8-quantized copy of the model
        _model = TextEmbedding(MODEL, threads=None, **({"specific_model_path": d} if d else {}))
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))   # less padding per batch
    vs = list(_model.embed([texts[i] for i in order], batch_size=64))
    v = np.empty((len(texts), len(vs[0])), dtype=np.float32)
    for k, i in enumerate(order): v[i] = vs[k]
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    _cache[key] = v
    if disk: os.makedirs(disk, exist_ok=True); np.save(f"{disk}/{key}.npy", v)
    return v

# ---------- srt ----------
TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
def parse(path):
    # newline="": no universal-newline translation, which would turn "\r\r\n" (a common
    # mis-conversion) into a blank line between the timing and the text of every cue
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        return parse_text(f.read())

def parse_text(txt):
    txt = re.sub(r"\r*\n|\r", "\n", txt.lstrip("﻿"))
    cues = []
    for blk in re.split(r"\n\s*\n", txt.strip()):
        lines = blk.split("\n")
        for i, l in enumerate(lines):
            m = TS.search(l)
            if m:
                g = list(map(int, m.groups()))
                cues.append([g[0]*3600+g[1]*60+g[2]+g[3]/1000, g[4]*3600+g[5]*60+g[6]+g[7]/1000,
                             "\n".join(lines[i+1:]).strip()])
                break
    cues.sort(key=lambda c: c[0])
    return cues

def fmt(t):
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"

def write(path, cues):
    with open(path, "w", encoding="utf-8") as f:
        for i, (s, e, x) in enumerate(cues, 1):
            f.write(f"{i}\n{fmt(s)} --> {fmt(e)}\n{x}\n\n")

def clean(t):
    t = re.sub(r"\{[^}]*\}|<[^>]+>", " ", t)            # ass / html tags
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)          # SDH [music] (sighs)
    t = re.sub(r"(?m)^\s*-\s*", " ", t)                  # dialogue dashes
    t = re.sub(r"(?m)^[A-Z][A-Z .'-]{1,20}:\s*", " ", t)  # SPEAKER:
    t = re.sub(r"[♪♫#*]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

# ---------- core ----------
def sync(tgt, ref, p=P, embed_fn=None):
    """Re-time `tgt` cues on `ref` cues. `embed_fn(texts) -> unit vectors` defaults to the model."""
    embed_fn = embed_fn or embed
    tt = [clean(c[2]) for c in tgt]; rt = [clean(c[2]) for c in ref]
    ti = [i for i, x in enumerate(tt) if len(x) >= 3]
    ri = [j for j, x in enumerate(rt) if len(x) >= 3]
    if len(ti) < 20 or len(ri) < 20:
        return None, {"status": "too_few_cues"}
    # reference units: single cues + merges of 2 consecutive cues
    ti = ti[::p.get("stride", 1)]
    units = [(j, rt[j]) for j in ri] + [(ri[k], rt[ri[k]] + " " + rt[ri[k+1]]) for k in range(len(ri)-1)]
    ve = embed_fn([tt[i] for i in ti])
    if p.get("merge", "embed") == "embed":
        vr = embed_fn([u[1] for u in units])
    else:
        v1 = embed_fn([rt[j] for j in ri]); v2 = v1[:-1] + v1[1:]
        v2 /= np.linalg.norm(v2, axis=1, keepdims=True) + 1e-9
        vr = np.vstack([v1, v2])
    sim = ve @ vr.T                                             # (n_tgt, n_units)
    # best score per (target cue, reference start cue)
    cand = []
    for a, i in enumerate(ti):
        row = sim[a]; order = np.argsort(-row)
        seen = set()
        for u in order[:p["topk"] * 2]:
            j = units[u][0]
            if j in seen or row[u] < p["min_sim"]: continue
            off = ref[j][0] - tgt[i][0]
            if abs(off) > p["max_offset"]: continue
            seen.add(j); cand.append((i, j, float(row[u])))
            if len(seen) >= p["topk"]: break
    if not cand:
        return None, {"status": "no_match"}
    # weighted longest chain, strictly increasing in i and j
    cand.sort(key=lambda c: (c[0], -c[1]))
    best = {}; prev = {}
    # Fenwick-like via sorted list of (j, cumulative score) maxima -> simple O(P log P) with bisect over j
    js, vals, ids = [], [], []            # monotone structure: increasing j, increasing value
    score = [0.0] * len(cand); back = [-1] * len(cand)
    # process by i; same-i candidates must not chain -> buffer updates per i
    k = 0
    while k < len(cand):
        i0 = cand[k][0]; group = []
        while k < len(cand) and cand[k][0] == i0:
            i, j, s = cand[k]
            pos = bisect.bisect_left(js, j) - 1
            score[k] = s + (vals[pos] if pos >= 0 else 0.0)
            back[k] = ids[pos] if pos >= 0 else -1
            group.append(k); k += 1
        for g in group:
            j = cand[g][1]; v = score[g]
            pos = bisect.bisect_left(js, j)
            if pos > 0 and vals[pos-1] >= v: continue
            # insert and remove dominated successors
            end = pos
            while end < len(js) and vals[end] <= v: end += 1
            js[pos:end] = [j]; vals[pos:end] = [v]; ids[pos:end] = [g]
    g = ids[-1] if ids else -1
    chain = []
    while g >= 0: chain.append(cand[g]); g = back[g]
    chain.reverse()
    # robust neighbourhood filter on offsets
    offs = np.array([ref[j][0] - tgt[i][0] for i, j, _ in chain])
    # an anchor is kept if it agrees with its left OR its right neighbours
    # (one-sided, so anchors right after a cut are not rejected)
    keep = []; nb = p["nb"]
    for a in range(len(chain)):
        left, right = offs[max(0, a - nb):a], offs[a + 1:a + 1 + nb]
        ok = any(len(side) >= 3 and abs(offs[a] - np.median(side)) <= p["max_dev"]
                 for side in (left, right))
        if ok: keep.append(a)
    anchors = [(tgt[chain[a][0]][0], offs[a]) for a in keep]
    if len(anchors) < 10:
        return None, {"status": "too_few_anchors", "anchors": len(anchors)}
    at = np.array([a[0] for a in anchors]); ao = np.array([a[1] for a in anchors])
    drift = best_drift(at, ao)                  # one frame-rate ratio for the whole file
    segs = segment(at, ao - drift * at, p)      # then piecewise-CONSTANT offsets
    # refine the common slope over all segments (segment-specific intercepts):
    # 25/23.976 vs 25/24 differ by 0.1 %, invisible between neighbours but 7 s over 2 h
    if True:   # also when drift == 0: a 0.1 % ratio (24 vs 23.976) is invisible to best_drift
        for thr in (3.0, 1.5, 0.8, 0.5, 0.5):     # iterate: inliers re-centred on the new slope
            num = den = 0.0
            for sg in segs:
                m = (at >= sg["t0"]) & (at <= sg["t1"])
                t, r = at[m], (ao - drift * at)[m]
                inl = np.abs(r - np.median(r)) < thr
                if inl.sum() < 5: continue
                t, r = t[inl], r[inl]
                num += np.sum((t - t.mean()) * (r - r.mean())); den += np.sum((t - t.mean()) ** 2)
            if den > 0: drift += num / den
    for sg in segs:
        m = (at >= sg["t0"]) & (at <= sg["t1"])
        sg["a"] = float(np.median((ao - drift * at)[m])); sg["b"] = drift
    def offset_at(t):
        for k, sg in enumerate(segs):
            if t <= sg["t1"]:
                if t >= sg["t0"] or k == 0: return sg["a"] + sg["b"] * t
                prev = segs[k-1]      # gap between two segments (a cut): nearest segment wins
                return (prev["a"] + prev["b"] * t) if t - prev["t1"] <= sg["t0"] - t else sg["a"] + sg["b"] * t
        sg = segs[-1]; return sg["a"] + sg["b"] * t
    if max(abs(offset_at(t)) for sg in segs for t in (sg["t0"], sg["t1"])) < p.get("deadband", 0):
        out = [[s, e, x] for s, e, x in tgt]    # already in sync: do not move it by the FR/VO bias
    else:
        out = [[s + offset_at(s), e + offset_at(s), x] for s, e, x in tgt]
    resid = np.concatenate([sg["resid"] for sg in segs])
    # no overlaps created at cut points
    for a in range(len(out) - 1):
        if out[a][1] > out[a+1][0] and out[a][0] < out[a+1][0]:
            out[a][1] = max(out[a][0] + 0.3, out[a+1][0] - 0.04)
    stats = {"status": "ok", "tgt_cues": len(tgt), "embeddable": len(ti), "chain": len(chain),
             "anchors": len(anchors), "coverage": round(len(anchors) / len(ti), 3),
             "mean_sim": round(float(np.mean([chain[a][2] for a in keep])), 3),
             "segments": len(segs),
             "seg": [{"t0": round(s["t0"], 1), "t1": round(s["t1"], 1), "n": s["n"],
                      "a": round(s["a"], 3), "drift_ppm": round(s["b"] * 1e6)} for s in segs],
             "resid_med": round(float(np.median(np.abs(resid))), 3),
             "max_abs_offset": round(max(abs(s["a"] + s["b"] * s["t0"]) for s in segs), 3)}
    return out, stats

def fit(t, o, allow_slope):
    """Robust line o = a + b t: least squares with iterative trimming."""
    m = np.ones(len(t), bool)
    for _ in range(4):
        a, b = float(np.median(o[m])), 0.0        # offsets are already de-drifted
        r = o - (a + b * t)
        thr = max(0.25, 2.5 * float(np.median(np.abs(r[m]))))
        m2 = np.abs(r) <= thr
        if m2.sum() < 3 or (m2 == m).all(): break
        m = m2
    return float(a), float(b), r[m]

def segment(at, ao, p, jump=0.8, confirm=6):
    """Greedy change-point detection: a new segment starts when `confirm` consecutive
    anchors agree with each other but not with the current segment's prediction."""
    bounds = [0]; i = 0; n = len(at)
    while i < n:
        s0 = bounds[-1]
        cur = slice(max(s0, i - 60), i)
        if i - s0 >= 3:
            tt, oo = at[cur], ao[cur]
            a, b, _ = fit(tt, oo, allow_slope=(i - s0) >= 20)
            pred = a + b * at[i]
            if abs(ao[i] - pred) > jump:
                nxt = ao[i:i + confirm]
                if len(nxt) == confirm and np.sum(np.abs(nxt - np.median(nxt)) <= jump / 2) >= confirm - 2 \
                        and abs(np.median(nxt) - pred) > jump:
                    bounds.append(i)
        i += 1
    bounds.append(n)
    while len(bounds) > 2:      # real cuts give long segments; a short one is a local mis-match
        short = [k for k in range(len(bounds) - 1) if at[bounds[k+1] - 1] - at[bounds[k]] < p.get("min_seg", 0)]
        if not short: break
        k = short[0]; del bounds[k if k > 0 else 1]
    segs = []
    for s0, s1 in zip(bounds[:-1], bounds[1:]):
        t, o = at[s0:s1], ao[s0:s1]
        a, b, r = fit(t, o, allow_slope=True)
        segs.append({"t0": float(t[0]), "t1": float(t[-1]), "n": int(s1 - s0), "a": a, "b": b, "resid": r})
    return segs

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        sys.exit(__doc__)
    out, st = sync(parse(argv[0]), parse(argv[1]))
    if out is not None:
        write(argv[2], out)
    print(json.dumps(st, ensure_ascii=False))
    return 0 if out is not None else 1


if __name__ == "__main__":
    sys.exit(main())
