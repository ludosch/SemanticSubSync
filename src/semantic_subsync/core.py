#!/usr/bin/env python3
"""Semantic subtitle sync: re-time a subtitle (e.g. FR) on a reference in another
language (e.g. the original EN) by matching cues on MEANING, not on timing shape.

1. clean + embed every cue with a multilingual sentence model (local, deterministic)
2. candidate pairs: for each target cue, top-k reference cues (single or 2-cue merge)
3. weighted longest increasing chain -> monotonic anchors
4. drop anchors whose offset disagrees with their neighbours (robust local median)
5. every target cue gets the smoothed offset of its anchor neighbourhood
6. at a cut, cues that land where the reference says nothing are dropped (a scene the video lacks)

Command line: see cli.py.
"""
import os, re, bisect
import numpy as np

# The sentence models. Their similarity scales differ, so the threshold for a candidate match
# (min_sim) belongs to the model and is never chosen on its own.
MODELS = {
    # averaged static token vectors (no transformer): its vectors take ~1 % of minilm's time, same
    # decisions on the benches; only the first `dims` dimensions are used (Matryoshka training).
    # The official float16 export: half the download of the float32 file, same decisions on the benches
    "static": dict(repo="sentence-transformers/static-similarity-mrl-multilingual-v1", min_sim=0.32, dims=512,
                   revision="b68f4122911bcffcd6e1f695f2d99cd6788972d8", table="onnx/model_fp16.onnx"),
    # a small multilingual transformer: much slower, a little better on some hard cases
    "minilm": dict(repo="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", min_sim=0.55),
}
DEFAULT_MODEL = os.environ.get("SEMSYNC_MODEL", "static")
P = dict(topk=3, nb=7, max_dev=1.0, max_offset=900.0,
         merge="mean",   # "embed": encode 2-cue merges; "mean": average the two cue vectors (free)
         stride=1,       # embed every Nth target cue only (anchors); all cues are still re-timed
         tgt_merge=True, # also match 2 consecutive target cues (a sentence split in two)
         min_seg=60.0,   # s: a segment shorter than this is a mis-match, not a cut -> merged into its neighbour
         deadband=0.5,   # s: a single constant correction smaller than this = natural FR/VO bias -> leave file untouched
         extra_lines="drop")  # lines the video has no room for (a scene or a recap it lacks, a credit):
                              # "drop" always (default); "keep" where the output and the reference are both silent
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

_models = {}; _cache = {}
def model_dir(model):
    """$SEMSYNC_MODEL_DIR/<model> when that folder exists (a local copy, e.g. the int8 minilm),
    else None: the model is downloaded from Hugging Face on first use."""
    root = os.environ.get("SEMSYNC_MODEL_DIR")
    d = root and os.path.join(root, model)
    return d if d and os.path.isdir(d) else None

def check_model(model):
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}: choose one of {', '.join(MODELS)}")
    return model

def embed(texts, model=None):
    import hashlib
    model = check_model(model or DEFAULT_MODEL)
    d = model_dir(model)
    key = hashlib.md5((model + MODELS[model].get("table", "") + (d or "") + "\x00" + "\x00".join(texts)).encode()).hexdigest()
    if key in _cache: return _cache[key]
    disk = os.environ.get("SEMSYNC_CACHE")
    if disk and os.path.exists(f"{disk}/{key}.npy"):
        _cache[key] = np.load(f"{disk}/{key}.npy"); return _cache[key]
    if model not in _models:
        _models[model] = (_load_static if model == "static" else _load_minilm)(MODELS[model], d)
    v = _models[model](texts)
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    _cache[key] = v
    if disk: os.makedirs(disk, exist_ok=True); np.save(f"{disk}/{key}.npy", v)
    return v

def _load_minilm(m, d):
    from fastembed import TextEmbedding
    model = TextEmbedding(m["repo"], threads=None, **({"specific_model_path": d} if d else {}))
    def emb(texts):
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))   # less padding per batch
        vs = list(model.embed([texts[i] for i in order], batch_size=64))
        v = np.empty((len(texts), len(vs[0])), dtype=np.float32)
        for k, i in enumerate(order): v[i] = vs[k]
        return v
    return emb

def _safetensors_table(path):
    """The float32 table of a safetensors file (a local copy of the full-precision model)."""
    import json
    raw = np.memmap(path, np.uint8, "r"); n = int.from_bytes(bytes(raw[:8]), "little")
    t = json.loads(bytes(raw[8:8 + n]))["embedding.weight"]; a, b = t["data_offsets"]
    assert t["dtype"] == "F32", t["dtype"]
    return raw[8 + n + a: 8 + n + b].view(np.float32).reshape(t["shape"])

def _onnx_table(path, name="embedding.weight"):
    """The tensor `name` of an ONNX file, found by walking its protobuf: the graph's initializers
    and the sub-graphs held by node attributes (the official fp16 export keeps the table in a Loop
    body). Memory-mapped; no onnx library needed."""
    raw = np.memmap(path, np.uint8, "r"); buf = memoryview(raw)
    def varint(i):
        x = s = 0
        while True:
            b = buf[i]; i += 1; x |= (b & 0x7F) << s; s += 7
            if b < 0x80: return x, i
    def fields(i, end):   # (field number, value) for varints, (field number, start, end) for bytes
        while i < end:
            key, i = varint(i); f, w = key >> 3, key & 7
            if w == 0: v, i = varint(i); yield f, v, None
            elif w == 2: n, i = varint(i); yield f, i, i + n; i += n
            elif w == 1: i += 8
            elif w == 5: i += 4
            else: raise ValueError(f"{path}: unexpected protobuf wire type {w}")
    def tensors(i, end):  # GraphProto: initializer = 5, node = 1 -> attribute = 5 -> g = 6, graphs = 11
        for f, a, b in fields(i, end):
            if b is None: continue
            if f == 5: yield a, b
            elif f == 1:
                for f2, a2, b2 in fields(a, b):
                    if f2 == 5 and b2 is not None:
                        for f3, a3, b3 in fields(a2, b2):
                            if f3 in (6, 11) and b3 is not None: yield from tensors(a3, b3)
    for f, a, b in fields(0, len(buf)):
        if f != 7 or b is None: continue   # ModelProto.graph
        for ta, tb in tensors(a, b):        # TensorProto: dims = 1, data_type = 2, name = 8, raw_data = 9
            dims, dtype, tname, data = [], None, None, None
            for f2, x, y in fields(ta, tb):
                if f2 == 1:
                    if y is None: dims.append(x)
                    else:
                        j = x
                        while j < y: v, j = varint(j); dims.append(v)
                elif f2 == 2: dtype = x
                elif f2 == 8: tname = bytes(buf[x:y]).decode()
                elif f2 == 9: data = (x, y)
            if tname == name:
                types = {1: np.float32, 10: np.float16}
                if dtype not in types or data is None:
                    raise ValueError(f"{path}: {name} has data type {dtype} or no raw data")
                return raw[data[0]:data[1]].view(types[dtype]).reshape(dims)
    raise ValueError(f"{path}: no tensor {name}")

def _load_static(m, d):
    """A cue's vector is the mean of its token vectors. The table is the official float16 export
    (onnx/model_fp16.onnx at a fixed revision), or a local copy (model_fp16.onnx, or the
    full-precision model.safetensors). Only the first `dims` columns are kept in memory; a
    looked-up row is turned into float32 before the mean."""
    from tokenizers import Tokenizer
    if d:
        tok_file = os.path.join(d, "tokenizer.json")
        table_file = next((p for p in (os.path.join(d, f) for f in ("model_fp16.onnx", "model.safetensors"))
                           if os.path.exists(p)), os.path.join(d, "model_fp16.onnx"))
    else:
        from huggingface_hub import hf_hub_download
        tok_file = hf_hub_download(m["repo"], "0_StaticEmbedding/tokenizer.json", revision=m["revision"])
        table_file = hf_hub_download(m["repo"], m["table"], revision=m["revision"])
    tok = Tokenizer.from_file(tok_file)
    full = _safetensors_table(table_file) if table_file.endswith(".safetensors") else _onnx_table(table_file)
    table = np.ascontiguousarray(full[:, :m["dims"]]); del full
    def emb(texts):
        v = np.zeros((len(texts), table.shape[1]), np.float32)
        for i, e in enumerate(tok.encode_batch(texts, add_special_tokens=False)):
            if e.ids: v[i] = table[e.ids].astype(np.float32).mean(0)
        return v
    return emb

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
def params(model=None, **over):
    """The engine settings for `model`: P plus that model's min_sim, then `over`."""
    return {**P, "min_sim": MODELS[check_model(model or DEFAULT_MODEL)]["min_sim"], **over}

def sync(tgt, ref, p=None, embed_fn=None, model=None):
    """Re-time `tgt` cues on `ref` cues with `model` (default DEFAULT_MODEL). `p` overrides
    params(model); `embed_fn(texts) -> unit vectors` replaces the model (tests)."""
    p = params(model, **(p or {}))
    embed_fn = embed_fn or (lambda texts: embed(texts, model))
    tt = [clean(c[2]) for c in tgt]; rt = [clean(c[2]) for c in ref]
    ti = [i for i, x in enumerate(tt) if len(x) >= 3]
    ri = [j for j, x in enumerate(rt) if len(x) >= 3]
    if len(ti) < 20 or len(ri) < 20:
        return None, {"status": "too_few_cues"}
    # reference units: single cues + merges of 2 consecutive cues
    ti = ti[::p.get("stride", 1)]
    units = [(j, rt[j]) for j in ri] + [(ri[k], rt[ri[k]] + " " + rt[ri[k+1]]) for k in range(len(ri)-1)]
    # target units: single cues, plus merges of 2 consecutive cues when tgt_merge is on (a sentence
    # split in two cues: each half alone often scores below min_sim against the whole sentence)
    tunits = [(i, i) for i in ti]
    if p.get("tgt_merge", True):
        tunits += [(ti[k], ti[k+1]) for k in range(len(ti)-1)]
    if p.get("merge", "embed") == "embed":
        vr = embed_fn([u[1] for u in units])
        ve = embed_fn([tt[a] if a == b else tt[a] + " " + tt[b] for a, b in tunits])
    else:
        vr, ve = _with_pairs(embed_fn([rt[j] for j in ri])), _with_pairs(embed_fn([tt[i] for i in ti]))
        ve = ve[:len(tunits)]
    sim = ve @ vr.T                                             # (n_tgt_units, n_ref_units)
    # best score per (target start cue, reference start cue); `last` = last target cue covered
    best = {}
    for a, (i, last) in enumerate(tunits):
        row = sim[a]; order = np.argsort(-row)
        seen = set()
        for u in order[:p["topk"] * 2]:
            j = units[u][0]
            if j in seen or row[u] < p["min_sim"]: continue
            off = ref[j][0] - tgt[i][0]
            if abs(off) > p["max_offset"]: continue
            seen.add(j)
            if (i, j) not in best or row[u] > best[(i, j)][2]:
                best[(i, j)] = (i, j, float(row[u]), last)
            if len(seen) >= p["topk"]: break
    cand = list(best.values())
    if not cand:
        return None, {"status": "no_match"}
    # weighted longest chain, strictly increasing in i and j
    cand.sort(key=lambda c: (c[0], -c[1]))
    # Fenwick-like via sorted list of (j, cumulative score) maxima -> simple O(P log P) with bisect over j
    js, vals, ids = [], [], []            # monotone structure: increasing j, increasing value
    score = [0.0] * len(cand); back = [-1] * len(cand)
    # process by i; same-i candidates must not chain -> buffer updates per i
    k = 0
    while k < len(cand):
        i0 = cand[k][0]; group = []
        while k < len(cand) and cand[k][0] == i0:
            i, j, s, _ = cand[k]
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
    offs = np.array([ref[j][0] - tgt[i][0] for i, j, _, _ in chain])
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
    ends = {s: e for s, e, _ in tgt}
    spoken = sorted((s, e) for s, e, _ in ref); starts = [s for s, _ in spoken]
    def place(s, e):
        """(offset, None) for a cue, or (None, candidate offsets) for an EXTRA line, one the video
        has no room for. Between two segments (a cut) the cue may follow either one; it is placed
        only where it neither runs into the other segment nor lands where the reference says
        nothing: else it belongs to a scene the video does not have. Before the first segment, a
        cue that would start before 0 s belongs to a part the video lacks (typically a "Previously
        on" recap); its candidates are the first segment's offset, then its own time (a credit)."""
        if s < segs[0]["t0"]:
            o = offset_at(s)
            return (o, None) if s + o >= 0 else (None, [o, 0.0])
        for prev, nxt in zip(segs, segs[1:]):
            if prev["t1"] < s < nxt["t0"]:
                end = ends[prev["t1"]] + prev["a"] + prev["b"] * prev["t1"]
                start = nxt["t0"] + nxt["a"] + nxt["b"] * nxt["t0"]
                fits = sorted([(s - prev["t1"], prev["a"] + prev["b"] * s, lambda o: e + o <= start),
                               (nxt["t0"] - s, nxt["a"] + nxt["b"] * s, lambda o: s + o >= end)],
                              key=lambda f: f[0])                                # nearest segment first
                for _, o, room in fits:
                    k = bisect.bisect_left(starts, e + o)
                    if room(o) and any(se > s + o for _, se in spoken[max(0, k - 5):k]):
                        return o, None
                return None, [o for _, o, _ in fits]
        return offset_at(s), None
    kept_extra = 0
    if max(abs(offset_at(t)) for sg in segs for t in (sg["t0"], sg["t1"])) < p.get("deadband", 0):
        out = [[s, e, x] for s, e, x in tgt]    # already in sync: do not move it by the FR/VO bias
    else:
        placed = [(i, place(s, e)) for i, (s, e, _) in enumerate(tgt)]
        # a cue that would end before 0 s cannot be shown (the video starts later); one that
        # straddles 0 s starts at 0
        moved = sorted(([max(0.0, tgt[i][0] + o), tgt[i][1] + o, tgt[i][2]], i)
                       for i, (o, _) in placed if o is not None and tgt[i][1] + o > 0.3)
        # trim only the overlaps created at cut points: the file's own overlaps (two speakers,
        # a sign over dialogue) are left as they are
        for (a, i), (b, j) in zip(moved, moved[1:]):
            if a[1] > b[0] and a[0] < b[0] and not (tgt[i][1] > tgt[j][0] and tgt[i][0] < tgt[j][0]):
                a[1] = max(a[0] + 0.3, b[0] - 0.04)
        out = [c for c, _ in moved]
        if p.get("extra_lines", "drop") == "keep":
            # Extra lines are kept only where nothing is shown and nothing is said: they never
            # overlap another line, nor dialogue that the reference has. Consecutive extra lines
            # (less than 10 s apart) form a block, kept whole or not at all: a credit fits in a
            # silence, a recap or a scene the video lacks never does, and a few of its lines
            # scattered in the pauses of the dialogue would make no sense.
            busy = sorted([(c[0], c[1]) for c in out] + spoken)
            def free(a, b):
                k = bisect.bisect_left(busy, (b,))
                return a >= 0 and not any(be > a for _, be in busy[max(0, k - 8):k])
            extras = [(i, c) for i, (_, c) in placed if c]
            blocks = []
            for i, c in extras:
                if blocks and tgt[i][0] - tgt[blocks[-1][-1][0]][1] < 10.0:
                    blocks[-1].append((i, c))
                else:
                    blocks.append([(i, c)])
            for block in blocks:
                for n in range(len(block[0][1])):          # the same candidate for the whole block
                    lines = [[tgt[i][0] + c[n], tgt[i][1] + c[n], tgt[i][2]] for i, c in block]
                    if all(free(a, b) for a, b, _ in lines):
                        out += lines; kept_extra += len(lines)
                        for a, b, _ in lines:
                            bisect.insort(busy, (a, b))
                        break
            out.sort()
    resid = np.concatenate([sg["resid"] for sg in segs])
    stats = {"status": "ok", "tgt_cues": len(tgt), "dropped": len(tgt) - len(out), "kept_extra": kept_extra,
             "embeddable": len(ti), "chain": len(chain),
             "anchors": len(anchors),
             "coverage": round(len({c for a in keep for c in chain[a][::3]}) / len(ti), 3),
             "mean_sim": round(float(np.mean([chain[a][2] for a in keep])), 3),
             "segments": len(segs),
             "seg": [{"t0": round(s["t0"], 1), "t1": round(s["t1"], 1), "n": s["n"],
                      "a": round(s["a"], 3), "drift_ppm": round(s["b"] * 1e6)} for s in segs],
             "resid_med": round(float(np.median(np.abs(resid))), 3),
             "max_abs_offset": round(max(abs(s["a"] + s["b"] * t) for s in segs for t in (s["t0"], s["t1"])), 3)}
    return out, stats

def _with_pairs(v):
    """Unit vectors of single items followed by the normalised mean of each consecutive pair."""
    v2 = v[:-1] + v[1:]
    v2 /= np.linalg.norm(v2, axis=1, keepdims=True) + 1e-9
    return np.vstack([v, v2])

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

MIN_COVERAGE = 0.25    # share of target cues anchored; below it the reference does not say the same thing


def resync(tgt, ref, p=None, embed_fn=None, min_coverage=MIN_COVERAGE, model=None):
    """`sync` plus the decision. Returns (status, cues, stats), status being one of
    "corrected" (cues = re-timed target), "in_sync" (cues = None: nothing to change) or
    "unsure" (cues = None: too few lines match the reference, so it cannot be trusted for
    this target and the file is left alone)."""
    out, st = sync(tgt, ref, p, embed_fn, model)
    if out is None or (st.get("coverage") or 0) < min_coverage:
        return "unsure", None, st
    if len(out) == len(tgt) and all(abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6 for a, b in zip(out, tgt)):
        return "in_sync", None, st
    return "corrected", out, st
