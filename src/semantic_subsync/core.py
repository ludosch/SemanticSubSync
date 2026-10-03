"""Semantic subtitle sync: re-time a subtitle (e.g. FR) on a reference in another
language (e.g. the original EN) by matching cues on MEANING, not on timing shape.

1. clean + embed every cue with a multilingual sentence model (local, deterministic)
2. candidates: for each target cue (and pair of consecutive cues), its top-k reference cues
   (single cues and pairs of consecutive cues) above the model's min_sim
3. heaviest chain strictly increasing on both sides -> monotonic anchors
4. drop anchors whose offset disagrees with their neighbours on both sides (local median)
5. one frame-rate ratio for the whole file (drift), then piecewise-CONSTANT offsets: segments
   split where the offset jumps for good (a cut); the common slope is refined over all segments
6. every target cue takes its segment's offset; between two segments it follows the nearest
   one that leaves room for it. A cue the video has no room for (a scene or a recap it lacks,
   a credit) is an extra line: dropped, or kept only where nothing is shown nor said
   (extra_lines="keep"). A correction smaller than the deadband leaves the file untouched.

Command line: see cli.py.
"""
import bisect, collections, gc, hashlib, os, re, types
from itertools import accumulate
import numpy as np

# The sentence models. Their similarity scales differ, so the threshold for a candidate match
# (min_sim) belongs to the model and is never chosen on its own.
MODELS = {
    # averaged static token vectors (no transformer): its vectors take ~1 % of minilm's time, same
    # decisions on the benches; only the first `dims` dimensions are used (Matryoshka training).
    # The official float16 export: half the download of the float32 file, same decisions on the benches
    "static": dict(repo="sentence-transformers/static-similarity-mrl-multilingual-v1", min_sim=0.32, dims=512,
                   revision="b68f4122911bcffcd6e1f695f2d99cd6788972d8", table="onnx/model_fp16.onnx",
                   sha256={"0_StaticEmbedding/tokenizer.json": "11aaf894a4ccf3d95e8830e27c0f8152791fbbff2b988e29a265580b86edd216",
                           "onnx/model_fp16.onnx": "fcdf6c63211755d3e79c2e75a280e5826f1c0c002d90c9dd60519af9503352ab"}),
    # a small multilingual transformer: much slower, a little better on some hard cases
    "minilm": dict(repo="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", min_sim=0.55),
}
DEFAULT_MODEL = os.environ.get("SEMSYNC_MODEL", "static")
# The engine settings, read-only: params() returns a copy with overrides.
P = types.MappingProxyType(dict(
    topk=3, nb=7, max_dev=1.0, max_offset=900.0,
    merge="mean",   # "embed": encode 2-cue merges; "mean": average the two cue vectors (free)
    stride=1,       # embed every Nth target cue only (anchors); all cues are still re-timed
    tgt_merge=True, # also match 2 consecutive target cues (a sentence split in two)
    min_seg=60.0,   # s: a segment shorter than this is a mis-match, not a cut -> merged into its neighbour
    deadband=0.5,   # s: a single constant correction smaller than this = natural FR/VO bias -> leave file untouched
    extra_lines="drop"))  # lines the video has no room for (a scene or a recap it lacks, a credit):
                          # "drop" always (default); "keep" where the output and the reference are both silent
CHOICES = {"merge": ("embed", "mean"), "extra_lines": ("keep", "drop")}
MIN_COVERAGE = 0.25    # share of target cues anchored; below it the reference does not say the same thing

# Fixed constants of the decision path (not settings: the benches were run with these values).
MIN_TEXT = 3           # characters of cleaned text below which a cue ("Oh.", "...") carries no meaning to match
MIN_CUES = 20          # embeddable cues needed on each side: fewer is a forced/signs track or a fragment
MIN_ANCHORS = 10       # fewer consistent anchors: no trustworthy correction
SCAN = 2               # rows scanned per target unit = topk * SCAN: a reference cue also appears in 2 merges
MIN_SIDE = 3           # neighbours needed on one side of an anchor to judge its offset
NO_DRIFT_SHARE = 0.95  # "no drift" wins unless a ratio explains clearly more anchor pairs than it
MIN_FIT = 3            # anchors needed to predict a segment's offset
FIT_WINDOW = 60        # anchors before the current one used to predict it: follows the latest offset
JUMP, CONFIRM = 0.8, 6 # s, anchors: a cut is an offset jump > JUMP that CONFIRM anchors in a row agree on
SLOPE_TRIM = (3.0, 1.5, 0.8, 0.5, 0.5)  # s: inlier window of each slope refinement pass, narrowing
MIN_SLOPE_INLIERS = 5  # a segment with fewer inliers does not weigh in the common slope
MIN_SHOWN = 0.3        # s: shortest visible cue (after a trim, or what is left of it after 0 s)
TRIM_GAP = 0.04        # s: gap left before the next cue when trimming an overlap created at a cut (a frame)
BLOCK_GAP = 10.0       # s: extra lines closer than this form one block, kept whole or not at all

# Drift only comes from frame-rate conversions, i.e. a handful of ratios for the whole file.
FPS = [23.976, 24.0, 25.0, 29.97, 30.0]
DRIFTS = sorted({round(a / b - 1, 6) for a in FPS for b in FPS if a / b - 1 and abs(a / b - 1) < 0.3} | {0.0})

Cand = collections.namedtuple("Cand", "i j sim last")   # target cue i (to `last`) matches reference cue j


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
    if scores[0.0] >= NO_DRIFT_SHARE * best: return 0.0
    return max(DRIFTS, key=lambda b: (scores[b], -abs(b)))

# ---------- models ----------
CACHE_SIZE = 8                       # embeddings kept in memory (a file's cues: ~2 MB each)
_models = {}                         # (model, model_dir) -> embedding function
_cache = collections.OrderedDict()   # LRU: hash of (model, texts) -> unit vectors

def unload():
    """Free the loaded models (0.2 to 0.5 GB each) and the embedding cache. True if a model was loaded."""
    loaded = bool(_models)
    _models.clear(); _cache.clear()
    if loaded: gc.collect()
    return loaded

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
    """Unit vectors of `texts` (float32, one row each). Kept in a small in-memory LRU cache and,
    when $SEMSYNC_CACHE is set, in that folder (one .npy per call)."""
    model = check_model(model or DEFAULT_MODEL)
    d = model_dir(model)
    key = hashlib.md5((model + MODELS[model].get("table", "") + (d or "") + "\x00" + "\x00".join(texts)).encode(),
                      usedforsecurity=False).hexdigest()
    if key in _cache:
        _cache.move_to_end(key); return _cache[key]
    disk = os.environ.get("SEMSYNC_CACHE")
    v = _load_cached(os.path.join(disk, f"{key}.npy")) if disk else None
    if v is None:
        if (model, d) not in _models:
            _models[(model, d)] = (_load_static if model == "static" else _load_minilm)(MODELS[model], d)
        v = _models[(model, d)](texts)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
        if disk: _save_cached(os.path.join(disk, f"{key}.npy"), v)
    _cache[key] = v
    while len(_cache) > CACHE_SIZE: _cache.popitem(last=False)
    return v

def _load_cached(path):
    """A cached embedding, or None (missing or unreadable: a cache miss, recomputed)."""
    try:
        return np.load(path)
    except (OSError, ValueError, EOFError):
        return None

def _save_cached(path, v):
    """Written whole or not at all (temporary file renamed), so a reader never loads half an array."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as f: np.save(f, v)
        os.replace(tmp, path)
    except OSError:
        pass                                       # a cache that cannot be written is only slower
    finally:
        if os.path.exists(tmp): os.remove(tmp)

def _load_minilm(m, d):
    try:
        from fastembed import TextEmbedding
    except ImportError:
        raise ImportError("the minilm model needs fastembed: pip install 'semantic-subsync[model]'") from None
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
    if t["dtype"] != "F32":
        raise ValueError(f"{path}: embedding.weight has data type {t['dtype']}, expected F32")
    return raw[8 + n + a: 8 + n + b].view(np.float32).reshape(t["shape"])

def _onnx_table(path, name="embedding.weight"):
    """The tensor `name` of an ONNX file, found by walking its protobuf: the graph's initializers
    and the sub-graphs held by node attributes (the official fp16 export keeps the table in a Loop
    body). Memory-mapped; no onnx library needed."""
    raw = np.memmap(path, np.uint8, "r"); buf = memoryview(raw)
    def truncated():
        return ValueError(f"{path}: truncated or not an ONNX file")
    def varint(i):
        x = s = 0
        while True:
            if i >= len(buf): raise truncated()
            b = buf[i]; i += 1; x |= (b & 0x7F) << s; s += 7
            if b < 0x80: return x, i
    def fields(i, end):   # (field number, value) for varints, (field number, start, end) for bytes
        while i < end:
            key, i = varint(i); f, w = key >> 3, key & 7
            if w == 0: v, i = varint(i); yield f, v, None
            elif w == 2:
                n, i = varint(i)
                if i + n > end: raise truncated()
                yield f, i, i + n; i += n
            elif w == 1: i += 8
            elif w == 5: i += 4
            else: raise ValueError(f"{path}: unexpected protobuf wire type {w}")
            if i > end: raise truncated()
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
                types_ = {1: np.float32, 10: np.float16}
                if dtype not in types_ or data is None:
                    raise ValueError(f"{path}: {name} has data type {dtype} or no raw data")
                return raw[data[0]:data[1]].view(types_[dtype]).reshape(dims)
    raise ValueError(f"{path}: no tensor {name}")

def _download(m, filename):
    """A file of the model's Hugging Face repository at its fixed revision, checked against its
    SHA-256, kept in $HF_HOME/semantic-subsync (default ~/.cache/huggingface/semantic-subsync).
    A file only takes its final name once verified, so a file found there is complete."""
    import urllib.request
    root = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    path = os.path.join(root, "semantic-subsync", m["repo"].replace("/", "--"), m["revision"], filename)
    if os.path.exists(path): return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    url = f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{filename}"
    tmp, h = f"{path}.{os.getpid()}.part", hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                h.update(chunk); f.write(chunk)
        if h.hexdigest() != m["sha256"][filename]:
            raise ValueError(f"{url}: the download does not match its SHA-256")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    return path

def _load_static(m, d):
    """A cue's vector is the mean of its token vectors. The table is the official float16 export
    (onnx/model_fp16.onnx at a fixed revision), or a local copy (model_fp16.onnx, or the
    full-precision model.safetensors). Only the first `dims` columns are kept in memory; a
    looked-up row is turned into float32 before the mean."""
    try:
        from tokenizers import Tokenizer
    except ImportError:
        raise ImportError("the static model needs tokenizers: pip install 'semantic-subsync[static]'") from None
    if d:
        tok_file = os.path.join(d, "tokenizer.json")
        table_file = next((p for p in (os.path.join(d, f) for f in ("model_fp16.onnx", "model.safetensors"))
                           if os.path.exists(p)), os.path.join(d, "model_fp16.onnx"))
    else:
        tok_file, table_file = (_download(m, f) for f in ("0_StaticEmbedding/tokenizer.json", m["table"]))
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
# hours are optional ("01:02.500", seen in WebVTT-style files); the fraction has 1 to 3+ digits
TS = re.compile(r"(?:(\d+):)?(\d+):(\d+)[,.](\d+)\s*-->\s*(?:(\d+):)?(\d+):(\d+)[,.](\d+)")

def parse(path, lang=None):
    """The cues of an SRT file in any encoding: UTF-8/UTF-16, else the code page of `lang`
    (e.g. 'ru'; default: the tag in the file name, as in Movie.ru.srt). Same as media.read_srt."""
    from .media import read_srt     # media imports core
    return read_srt(path, lang)

def _seconds(h, m, s, frac):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(frac) / 10 ** len(frac)

def parse_text(txt):
    """The cues of SRT text as [[start, end, text], ...] sorted by start; blocks without a timing are skipped."""
    txt = re.sub(r"\r*\n|\r", "\n", txt.lstrip("﻿"))
    cues = []
    for blk in re.split(r"\n\s*\n", txt.strip()):
        lines = blk.split("\n")
        for i, l in enumerate(lines):
            m = TS.search(l)
            if m:
                g = m.groups()
                cues.append([_seconds(*g[:4]), _seconds(*g[4:]), "\n".join(lines[i+1:]).strip()])
                break
    cues.sort(key=lambda c: c[0])
    return cues

def fmt(t):
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"

def write(path, cues):
    """Write `cues` as SRT (UTF-8, "\\n" line ends on every platform). The file is written under a
    temporary name then renamed: a reader never sees half a file."""
    path = os.fspath(path); tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for i, (s, e, x) in enumerate(cues, 1):
                f.write(f"{i}\n{fmt(s)} --> {fmt(e)}\n{x}\n\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.remove(tmp)

def clean(t):
    t = re.sub(r"\{[^}]*\}|<[^>]+>", " ", t)            # ass / html tags
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)          # SDH [music] (sighs)
    t = re.sub(r"(?m)^\s*-\s*", " ", t)                  # dialogue dashes
    t = re.sub(r"(?m)^[A-Z][A-Z .'-]{1,20}:\s*", " ", t)  # SPEAKER:
    t = re.sub(r"[♪♫#*]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

# ---------- core ----------
def params(model=None, **over):
    """The engine settings for `model`: P plus that model's min_sim, then `over`. Unknown
    settings and invalid choices raise ValueError."""
    unknown = set(over) - set(P) - {"min_sim"}
    if unknown:
        raise ValueError(f"unknown engine setting(s): {', '.join(sorted(unknown))}")
    p = {**P, "min_sim": MODELS[check_model(model or DEFAULT_MODEL)]["min_sim"], **over}
    for k, ok in CHOICES.items():
        if p[k] not in ok:
            raise ValueError(f"{k} must be one of {', '.join(ok)}, not {p[k]!r}")
    for k in ("topk", "nb", "stride"):
        if not isinstance(p[k], int) or p[k] < 1:
            raise ValueError(f"{k} must be a positive integer, not {p[k]!r}")
    return p

def embeddable(cues):
    """(indexes, texts): the cleaned text of every cue, and the indexes of the cues with enough
    text to be matched."""
    texts = [clean(c[2]) for c in cues]
    return [i for i, x in enumerate(texts) if len(x) >= MIN_TEXT], texts

def similarity(tgt, ref, model=None, embed_fn=None):
    """(ti, ri, sim): the embeddable target and reference cue indexes and their cosine matrix,
    one cue against one cue (sim[a, b] for tgt[ti[a]] and ref[ri[b]])."""
    embed_fn = embed_fn or (lambda texts: embed(texts, model))
    (ti, tt), (ri, rt) = embeddable(tgt), embeddable(ref)
    return ti, ri, embed_fn([tt[i] for i in ti]) @ embed_fn([rt[j] for j in ri]).T

def sync(tgt, ref, p=None, embed_fn=None, model=None):
    """Re-time `tgt` cues on `ref` cues with `model` (default DEFAULT_MODEL). `p` overrides
    params(model); `embed_fn(texts) -> unit vectors` replaces the model (tests).
    Returns (cues, stats), or (None, stats) when no correction can be computed."""
    p = params(model, **(p or {}))
    embed_fn = embed_fn or (lambda texts: embed(texts, model))
    (ti, tt), (ri, rt) = embeddable(tgt), embeddable(ref)
    if len(ti) < MIN_CUES or len(ri) < MIN_CUES:
        return None, {"status": "too_few_cues"}
    ti = ti[::p["stride"]]
    cand = _candidates(tgt, ref, tt, rt, ti, ri, p, embed_fn)
    if not cand:
        return None, {"status": "no_match"}
    chain = _heaviest_chain(cand)
    offs = np.array([ref[c.j][0] - tgt[c.i][0] for c in chain])
    keep = _consistent(offs, p)
    if len(keep) < MIN_ANCHORS:
        return None, {"status": "too_few_anchors", "anchors": len(keep)}
    ai = [chain[a].i for a in keep]                       # target cue of each anchor
    at = np.array([tgt[i][0] for i in ai]); ao = offs[keep]
    segs = _offset_model(at, ao, p)
    out, kept_extra = _retime(tgt, ref, segs, ai, p)
    resid = np.concatenate([sg["resid"] for sg in segs])
    stats = {"status": "ok", "tgt_cues": len(tgt), "dropped": len(tgt) - len(out), "kept_extra": kept_extra,
             "embeddable": len(ti), "chain": len(chain),
             "anchors": len(keep),
             "coverage": round(len({x for a in keep for x in (chain[a].i, chain[a].last)}) / len(ti), 3),
             "mean_sim": round(float(np.mean([chain[a].sim for a in keep])), 3),
             "segments": len(segs),
             "seg": [{"t0": round(s["t0"], 1), "t1": round(s["t1"], 1), "n": s["n"],
                      "a": round(s["a"], 3), "drift_ppm": round(s["b"] * 1e6)} for s in segs],
             "resid_med": round(float(np.median(np.abs(resid))), 3),
             "max_abs_offset": round(max(abs(s["a"] + s["b"] * t) for s in segs for t in (s["t0"], s["t1"])), 3)}
    return out, stats

def _candidates(tgt, ref, tt, rt, ti, ri, p, embed_fn):
    """Candidate anchors Cand(i, j, sim, last): for each target unit (cue i, or cues i..last when a
    sentence is split in two), its `topk` best reference cues j above min_sim, a reference unit
    being one cue or two consecutive ones (counted for its first cue). One per (i, j): the best."""
    units = [(j, rt[j]) for j in ri] + [(ri[k], rt[ri[k]] + " " + rt[ri[k+1]]) for k in range(len(ri)-1)]
    # target units: single cues, plus merges of 2 consecutive cues when tgt_merge is on (a sentence
    # split in two cues: each half alone often scores below min_sim against the whole sentence)
    tunits = [(i, i) for i in ti]
    if p["tgt_merge"]:
        tunits += [(ti[k], ti[k+1]) for k in range(len(ti)-1)]
    if p["merge"] == "embed":
        vr = embed_fn([u[1] for u in units])
        ve = embed_fn([tt[a] if a == b else tt[a] + " " + tt[b] for a, b in tunits])
    else:
        vr, ve = _with_pairs(embed_fn([rt[j] for j in ri])), _with_pairs(embed_fn([tt[i] for i in ti]))
        ve = ve[:len(tunits)]
    sim = ve @ vr.T                                             # (n_tgt_units, n_ref_units)
    best = {}
    for a, (i, last) in enumerate(tunits):
        row = sim[a]; order = np.argsort(-row)
        seen = set()
        for u in order[:p["topk"] * SCAN]:
            j = units[u][0]
            if j in seen or row[u] < p["min_sim"]: continue
            if abs(ref[j][0] - tgt[i][0]) > p["max_offset"]: continue
            seen.add(j)
            if (i, j) not in best or row[u] > best[(i, j)].sim:
                best[(i, j)] = Cand(i, j, float(row[u]), last)
            if len(seen) >= p["topk"]: break
    return list(best.values())

def _with_pairs(v):
    """Unit vectors of single items followed by the normalised mean of each consecutive pair."""
    v2 = v[:-1] + v[1:]
    v2 /= np.linalg.norm(v2, axis=1, keepdims=True) + 1e-9
    return np.vstack([v, v2])

def _heaviest_chain(cand):
    """The candidates of highest total similarity strictly increasing in i and in j.
    Dynamic programming over candidates sorted by i, with a Pareto frontier of (j, best score of
    a chain ending at or before j): lookups by bisection, insertions by list slice."""
    cand = sorted(cand, key=lambda c: (c.i, -c.j))
    js, vals, ids = [], [], []            # frontier: increasing j, increasing score
    score = [0.0] * len(cand); back = [-1] * len(cand)
    # process by i; same-i candidates must not chain -> buffer updates per i
    k = 0
    while k < len(cand):
        i0 = cand[k].i; group = []
        while k < len(cand) and cand[k].i == i0:
            pos = bisect.bisect_left(js, cand[k].j) - 1
            score[k] = cand[k].sim + (vals[pos] if pos >= 0 else 0.0)
            back[k] = ids[pos] if pos >= 0 else -1
            group.append(k); k += 1
        for g in group:
            j = cand[g].j; v = score[g]
            pos = bisect.bisect_left(js, j)
            if pos > 0 and vals[pos-1] >= v: continue
            # insert and remove the entries it dominates (larger j, no better score)
            end = pos
            while end < len(js) and vals[end] <= v: end += 1
            js[pos:end] = [j]; vals[pos:end] = [v]; ids[pos:end] = [g]
    g = ids[-1] if ids else -1
    chain = []
    while g >= 0: chain.append(cand[g]); g = back[g]
    return chain[::-1]

def _consistent(offs, p):
    """Positions of the anchors whose offset agrees with the median of their `nb` left OR right
    neighbours (one-sided, so anchors right after a cut are not rejected)."""
    keep = []; nb = p["nb"]
    for a in range(len(offs)):
        left, right = offs[max(0, a - nb):a], offs[a + 1:a + 1 + nb]
        if any(len(side) >= MIN_SIDE and abs(offs[a] - np.median(side)) <= p["max_dev"] for side in (left, right)):
            keep.append(a)
    return keep

def _offset_model(at, ao, p):
    """Segments {t0, t1, n, k1, a, b, resid} of the correction offset = a + b t on the anchors
    (target time `at`, offset `ao`): one frame-rate drift b for the whole file, a constant a
    per segment. k1 = position of the segment's last anchor; resid = residuals of its anchors."""
    drift = best_drift(at, ao)                  # one frame-rate ratio for the whole file
    segs = segment(at, ao - drift * at, p)      # then piecewise-CONSTANT offsets
    # refine the common slope over all segments (segment-specific intercepts), also when drift
    # == 0: 25/23.976 vs 25/24 differ by 0.1 %, invisible to best_drift but 7 s over 2 h
    for thr in SLOPE_TRIM:                       # iterate: inliers re-centred on the new slope
        num = den = 0.0
        for sg in segs:
            m = (at >= sg["t0"]) & (at <= sg["t1"])
            t, r = at[m], (ao - drift * at)[m]
            inl = np.abs(r - np.median(r)) < thr
            if inl.sum() < MIN_SLOPE_INLIERS: continue
            t, r = t[inl], r[inl]
            num += np.sum((t - t.mean()) * (r - r.mean())); den += np.sum((t - t.mean()) ** 2)
        if den > 0: drift += num / den
    for sg in segs:
        m = (at >= sg["t0"]) & (at <= sg["t1"])
        r = (ao - drift * at)[m]
        sg["a"] = float(np.median(r)); sg["b"] = drift; sg["resid"] = r - sg["a"]
    return segs

def offset_at(segs, t):
    """The correction at target time `t`: its segment's, or in a gap between two segments (a cut)
    the nearest segment's."""
    for k, sg in enumerate(segs):
        if t <= sg["t1"]:
            if t >= sg["t0"] or k == 0: return sg["a"] + sg["b"] * t
            prev = segs[k-1]
            return (prev["a"] + prev["b"] * t) if t - prev["t1"] <= sg["t0"] - t else sg["a"] + sg["b"] * t
    sg = segs[-1]; return sg["a"] + sg["b"] * t

def _place(s, e, segs, seg_end, spoken, reach):
    """(offset, None) for the cue [s, e], or (None, candidate offsets) for an EXTRA line, one the
    video has no room for. Between two segments (a cut) the cue may follow either one; it is
    placed only where it neither runs into the other segment nor lands where the reference says
    nothing: else it belongs to a scene the video does not have. Before the first segment, a cue
    that would start before 0 s belongs to a part the video lacks (typically a "Previously on"
    recap); its candidates are the first segment's offset, then its own time (a credit).
    seg_end[k]: end of segment k's last anchor cue, re-timed. spoken: the reference's
    (start, end) sorted; reach: running maximum of their ends."""
    if s < segs[0]["t0"]:
        o = offset_at(segs, s)
        return (o, None) if s + o >= 0 else (None, [o, 0.0])
    for k, (prev, nxt) in enumerate(zip(segs, segs[1:])):
        if prev["t1"] < s < nxt["t0"]:
            end = seg_end[k]
            start = nxt["t0"] + nxt["a"] + nxt["b"] * nxt["t0"]
            fits = sorted([(s - prev["t1"], prev["a"] + prev["b"] * s, lambda o: e + o <= start),
                           (nxt["t0"] - s, nxt["a"] + nxt["b"] * s, lambda o: s + o >= end)],
                          key=lambda f: f[0])                                # nearest segment first
            for _, o, room in fits:
                n = bisect.bisect_left(spoken, (e + o,))    # reference cues starting before the cue ends
                if room(o) and n and reach[n - 1] > s + o:  # ... one of them still speaking when it starts
                    return o, None
            return None, [o for _, o, _ in fits]
    return offset_at(segs, s), None

def _retime(tgt, ref, segs, ai, p):
    """(re-timed cues, number of extra lines kept)."""
    if max(abs(offset_at(segs, t)) for sg in segs for t in (sg["t0"], sg["t1"])) < p["deadband"]:
        return [[s, e, x] for s, e, x in tgt], 0    # already in sync: do not move it by the FR/VO bias
    spoken = sorted((s, e) for s, e, _ in ref); reach = list(accumulate((e for _, e in spoken), max))
    seg_end = [tgt[ai[sg["k1"]]][1] + sg["a"] + sg["b"] * sg["t1"] for sg in segs]
    placed = [(i, _place(s, e, segs, seg_end, spoken, reach)) for i, (s, e, _) in enumerate(tgt)]
    # a cue that would end before 0 s cannot be shown (the video starts later); one that
    # straddles 0 s starts at 0
    moved = sorted(([max(0.0, tgt[i][0] + o), tgt[i][1] + o, tgt[i][2]], i)
                   for i, (o, _) in placed if o is not None and tgt[i][1] + o > MIN_SHOWN)
    # trim only the overlaps created at cut points: the file's own overlaps (two speakers,
    # a sign over dialogue) are left as they are
    for (a, i), (b, j) in zip(moved, moved[1:]):
        if a[1] > b[0] and a[0] < b[0] and not (tgt[i][1] > tgt[j][0] and tgt[i][0] < tgt[j][0]):
            a[1] = max(a[0] + MIN_SHOWN, b[0] - TRIM_GAP)
    out = [c for c, _ in moved]
    if p["extra_lines"] == "drop":
        return out, 0
    return _keep_extra(out, tgt, spoken, [(i, c) for i, (_, c) in placed if c])

def _keep_extra(out, tgt, spoken, extras):
    """Extra lines are kept only where nothing is shown and nothing is said: they never overlap
    another line, nor dialogue that the reference has. Consecutive extra lines (less than
    BLOCK_GAP apart) form a block, kept whole or not at all: a credit fits in a silence, a recap
    or a scene the video lacks never does, and a few of its lines scattered in the pauses of the
    dialogue would make no sense. `extras`: [(target index, candidate offsets)]."""
    busy = sorted([(c[0], c[1]) for c in out] + spoken)
    reach = list(accumulate((b for _, b in busy), max))
    def free(a, b):
        k = bisect.bisect_left(busy, (b,))          # busy intervals starting before b...
        return a >= 0 and not (k and reach[k - 1] > a)   # ...none still running at a
    blocks = []
    for i, c in extras:
        if blocks and tgt[i][0] - tgt[blocks[-1][-1][0]][1] < BLOCK_GAP:
            blocks[-1].append((i, c))
        else:
            blocks.append([(i, c)])
    kept = 0
    for block in blocks:
        for n in range(len(block[0][1])):          # the same candidate for the whole block
            lines = [[tgt[i][0] + c[n], tgt[i][1] + c[n], tgt[i][2]] for i, c in block]
            if all(free(a, b) for a, b, _ in lines):
                out += lines; kept += len(lines)
                for a, b, _ in lines:
                    bisect.insort(busy, (a, b))
                reach = list(accumulate((b for _, b in busy), max))
                break
    out.sort()
    return out, kept

def robust_level(o):
    """Median of `o` after iteratively trimming the values far from it (> 2.5 x the median
    absolute deviation, at least 0.25 s): the offset level of a stretch of anchors."""
    m = np.ones(len(o), bool)
    for _ in range(4):
        a = float(np.median(o[m]))
        r = o - a
        m2 = np.abs(r) <= max(0.25, 2.5 * float(np.median(np.abs(r[m]))))
        if m2.sum() < MIN_FIT or (m2 == m).all(): break
        m = m2
    return a

def segment(at, ao, p, jump=JUMP, confirm=CONFIRM):
    """Greedy change-point detection on de-drifted offsets: a new segment starts when `confirm`
    consecutive anchors agree with each other but not with the current segment's level.
    Returns [{t0, t1, n, k1}]: first / last anchor time, anchor count, last anchor position."""
    bounds = [0]; i = 0; n = len(at)
    while i < n:
        s0 = bounds[-1]
        if i - s0 >= MIN_FIT:
            pred = robust_level(ao[max(s0, i - FIT_WINDOW):i])
            if abs(ao[i] - pred) > jump:
                nxt = ao[i:i + confirm]
                if len(nxt) == confirm and np.sum(np.abs(nxt - np.median(nxt)) <= jump / 2) >= confirm - 2 \
                        and abs(np.median(nxt) - pred) > jump:
                    bounds.append(i)
        i += 1
    bounds.append(n)
    while len(bounds) > 2:      # real cuts give long segments; a short one is a local mis-match
        short = [k for k in range(len(bounds) - 1) if at[bounds[k+1] - 1] - at[bounds[k]] < p["min_seg"]]
        if not short: break
        k = short[0]; del bounds[k if k > 0 else 1]
    return [{"t0": float(at[s0]), "t1": float(at[s1 - 1]), "n": int(s1 - s0), "k1": int(s1 - 1)}
            for s0, s1 in zip(bounds[:-1], bounds[1:])]


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
