"""Synthetic subtitles for the tests: no model, no film extracts, fully deterministic.

Meaning is modelled by "concept" tokens such as `k17`: the reference cue for concept 17 reads
"line k17" and the target cue "cue k17". `fake_embed` maps each concept to a fixed random
unit vector, so a target cue and the reference cue that says the same thing get a cosine of ~0.9
whatever the wording, while unrelated cues score ~0.1, like a multilingual sentence model. A cue
carrying several concepts gets their normalised sum. A sentence split in two target cues is
written "k5a" + "k5b" while the reference's whole sentence is "k5a k5b k5": each half then scores
~0.55 against it and the two halves together ~0.8, as measured with the real MiniLM on French
halves vs English sentences (half: median 0.56-0.70, below 0.55 in 10/20; both halves: 0.76).

Words without a concept token are looked up in SYNONYMS, so "Oui." and "Yes." share one vector:
that is how repeated short replies are modelled.
"""
import hashlib, re
import numpy as np

DIM = 128
COMMON = 0.33                  # cos(unrelated) ~ COMMON² / (1 + COMMON²) ~ 0.1 (real MiniLM: ~0.07)
SYNONYMS = {"oui": "yes", "yes": "yes", "merci": "thanks", "thanks": "thanks",
            "non": "no", "no": "no", "quoi": "what", "what": "what"}


def _vec(key):
    seed = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    v = np.random.default_rng(seed).standard_normal(DIM)
    return v / np.linalg.norm(v)


MIN_SIM = 0.55                 # the fake embedder has MiniLM's similarity scale


def use_fake_model(monkeypatch):
    """Every model becomes the fake embedder, with the matching min_sim (each real model's own
    min_sim is checked against real sentences in test_model.py)."""
    monkeypatch.setattr("semantic_subsync.core.embed", fake_embed)
    from semantic_subsync import core
    for name, m in core.MODELS.items():
        monkeypatch.setitem(core.MODELS, name, {**m, "min_sim": MIN_SIM})


def fake_embed(texts, model=None):
    out = np.empty((len(texts), DIM), dtype=np.float32)
    for n, t in enumerate(texts):
        keys = re.findall(r"k\d+[ab]?", t) or [SYNONYMS.get(w, w) for w in re.findall(r"\w+", t.lower())]
        v = sum(_vec(k) for k in keys) if keys else _vec("")
        v = v / np.linalg.norm(v) + 0.35 * _vec("noise:" + t)    # wording: same meaning -> cos ~0.9
        # shared "it is dialogue" direction: unrelated lines score ~0.1, like the real model,
        # so that min_sim has something to reject
        v = v / np.linalg.norm(v) + COMMON * _vec("common")
        out[n] = v / np.linalg.norm(v)
    return out


def dialogue(n=300, seed=0, start=30.0):
    """Reference timeline: n cues with realistic durations, gaps and a few long silences.
    Returns [[start, end, concept_id], ...]."""
    rng = np.random.default_rng(seed)
    t, cues = start, []
    for k in range(n):
        dur = float(rng.uniform(1.0, 4.5))
        cues.append([t, t + dur, k])
        gap = float(rng.uniform(0.2, 3.0))
        if rng.random() < 0.04:
            gap += float(rng.uniform(15, 60))      # action scene without dialogue
        t += dur + gap
    return cues


def ref_cues(base, text="line k{k}"):
    return [[s, e, text.format(k=k)] for s, e, k in base]


def tgt_cues(base, warp=lambda t: t, drop=lambda s: False, text="cue k{k}"):
    """Target subtitle for the same dialogue: start times go through `warp` (the target's own
    timeline), durations follow its local slope; cues whose reference start satisfies `drop`
    (a scene missing from the target's edition) are absent."""
    out = []
    for s, e, k in base:
        if drop(s):
            continue
        ws = warp(s)
        out.append([ws, ws + (e - s) * (warp(s + 1e-3) - ws) / 1e-3, text.format(k=k)])
    return out


def cut(t0, length, then=lambda t: t):
    """The video's scene [t0, t0+length) is missing from the target's edition."""
    return dict(drop=lambda s: t0 <= s < t0 + length,
                warp=lambda t: then(t - length if t >= t0 + length else t))


def accuracy(out, base, tol=0.3):
    """Share of output cues that start within `tol` s of the reference cue saying the same thing."""
    truth = {k: s for s, _, k in base}
    hits = total = 0
    for s, _, x in out:
        m = re.search(r"k(\d+)", x)
        if m and int(m.group(1)) in truth:
            total += 1
            hits += abs(s - truth[int(m.group(1))]) <= tol
    return hits / total if total else 0.0
