"""Alignment engine on synthetic dialogues (fake embedder: tests the algorithm, not the model).

Each distortion of the bench has a case here. Thresholds are the bench's: a cue is right when it
starts within 300 ms of the reference cue that says the same thing.
"""
import copy

import pytest

from semantic_subsync import core
from synth import accuracy, cut, dialogue, fake_embed, ref_cues, tgt_cues

PAL = 25 / 23.976 - 1          # +4.27 %: 23.976 fps subtitle on a 25 fps (PAL) video


def run(tgt, ref, **p):
    return core.sync(tgt, ref, p={**core.P, **p}, embed_fn=fake_embed)


def piecewise(*steps):
    """warp(t) = t + offset of the last step whose time is <= t; steps = (t0, offset), ..."""
    def warp(t):
        off = 0.0
        for t0, o in steps:
            if t >= t0:
                off = o
        return t + off
    return warp


# ---------- distortions that must be corrected ----------

CASES = {
    "const_plus": dict(warp=lambda t: t + 7.3),
    "const_minus": dict(warp=lambda t: t - 12.0),
    "const_large": dict(warp=lambda t: t + 300.0),
    "fps_down": dict(warp=lambda t: t / (1 + PAL)),
    "fps_up": dict(warp=lambda t: t * (1 + PAL)),
    "fps_24": dict(warp=lambda t: t * 24 / 23.976),            # 0.1 %: 7 s over 2 h
    "fps_plus_offset": dict(warp=lambda t: t * (1 + PAL) + 3.0),
    "split_3": dict(warp=piecewise((0, 1.0), (900, -2.5), (1800, 4.0))),
    # a scene of the video is missing from the target's edition: its lines are absent and
    # everything after it comes earlier in the target
    "cut": cut(1200, 120),
    "cut_fps": cut(1200, 120, then=lambda t: t * (1 + PAL)),
    "two_cuts_offset": cut(600, 90, then=lambda t: cut(1500, 60)["warp"](t) + 2.0),
}


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_corrects_distortion(name, seed):
    base = dialogue(n=320, seed=seed)
    out, st = run(tgt_cues(base, **CASES[name]), ref_cues(base))
    assert out is not None, st
    assert accuracy(out, base) >= 0.95, (name, st)


def test_target_has_extra_scene():
    """The target's edition is longer: its extra lines have no reference; the rest must still align."""
    base = dialogue(n=300, seed=4)
    ref = ref_cues([c for c in base if not 100 <= c[2] < 115])          # absent from the video
    tgt = tgt_cues(base, warp=piecewise((0, 0.0), (base[115][0] - 1, 35.0)))
    out, st = run(tgt, ref)
    kept = [c for c in base if not 100 <= c[2] < 115]
    assert out is not None and accuracy(out, kept) >= 0.95, st


@pytest.mark.parametrize("seed", [4, 5, 6])
def test_lines_of_a_scene_the_video_lacks_are_dropped(seed):
    """The video is the shorter cut: the reference has no room for the extra scene, so its lines
    are left out instead of being stacked on the lines around it."""
    base = dialogue(n=300, seed=seed)
    t0 = base[100][0]
    ref = tgt_cues(base, **cut(t0, base[115][0] - t0), text="line k{k}")   # the video's timeline
    out, st = run(tgt_cues(base, warp=lambda t: t + 2.0), ref)
    kept = [c for c in base if not 100 <= c[2] < 115]
    assert sorted(int(x.split("k")[1]) for _, _, x in out) == [c[2] for c in kept], st
    assert st["dropped"] == 15, st
    assert out == sorted(out), "output must be in time order"


@pytest.mark.parametrize("recap", [10, 25])
def test_recap_the_video_lacks_is_dropped_not_stacked_at_zero(recap):
    """Real case, Game of Thrones S02E03: one release opens with a 90 s "Previously on" recap that
    the video lacks. The recap lines would start before 0 s: they are left out instead of being
    written at 00:00:00,000."""
    base = dialogue(n=300, seed=21, start=1.0)
    t0 = base[recap][0] - 0.5
    ref = [[s - t0, e - t0, f"line k{k}"] for s, e, k in base[recap:]]
    out, st = run(tgt_cues(base), ref)
    assert st["dropped"] == recap, st
    assert min(s for s, _, _ in out) > 0
    assert accuracy(out, [[s - t0, e - t0, k] for s, e, k in base[recap:]]) >= 0.95, st


def test_own_overlaps_do_not_count_as_a_correction():
    """Real case: a site watermark shown over the first line. In sync, the file stays untouched;
    the overlap is the file's own, not something to repair."""
    base = dialogue(n=300, seed=22)
    tgt = tgt_cues(base, warp=lambda t: t + 0.2)
    tgt.insert(0, [0.5, tgt[0][0] + 1.0, "www.example.net"])
    out, st = run(tgt, ref_cues(base))
    assert out == tgt, st


def test_correction_keeps_the_files_own_overlaps():
    """Two speakers overlapping in the target stay overlapping once shifted."""
    base = dialogue(n=300, seed=23)
    tgt = tgt_cues(base, warp=lambda t: t + 6.0)
    tgt[50][1] = tgt[51][0] + 1.0
    out, st = run(tgt, ref_cues(base))
    k = next(i for i, c in enumerate(out) if c[2] == tgt[50][2])
    assert out[k][1] - out[k][0] == pytest.approx(tgt[50][1] - tgt[50][0])
    assert accuracy(out, base) >= 0.95, st


# ---------- already in sync: never touch ----------

@pytest.mark.parametrize("bias", [0.0, 0.25, -0.4])
def test_in_sync_or_natural_bias_is_left_untouched(bias):
    """A constant shift below the deadband is the natural FR/VO bias, not an error."""
    base = dialogue(n=300, seed=5)
    tgt = tgt_cues(base, warp=lambda t: t + bias)
    out, st = run(tgt, ref_cues(base))
    assert out == tgt, st


def test_deadband_does_not_hide_a_real_offset():
    base = dialogue(n=300, seed=6)
    out, _ = run(tgt_cues(base, warp=lambda t: t + 0.9), ref_cues(base))
    assert accuracy(out, base, tol=0.15) >= 0.95


# ---------- refusal: the reference does not say the same thing ----------

def test_unrelated_reference_is_refused():
    base = dialogue(n=300, seed=7)
    other = [[s, e, f"line k{k + 10000}"] for s, e, k in dialogue(n=300, seed=8)]
    out, st = run(tgt_cues(base), other)
    assert out is None or st["coverage"] < 0.25, st


def test_commentary_like_reference_is_refused():
    """Like the Back to the Future commentary track: only a few lines in common."""
    base = dialogue(n=300, seed=9)
    ref = [[s, e, f"line k{k}" if k % 15 == 0 else f"line k{k + 10000}"] for s, e, k in base]
    out, st = run(tgt_cues(base, warp=lambda t: t + 20), ref)
    assert out is None or st["coverage"] < 0.25, st


def test_too_few_cues():
    base = dialogue(n=15, seed=10)
    out, st = run(tgt_cues(base), ref_cues(base))
    assert out is None and st["status"] == "too_few_cues"


def test_cues_without_text_are_ignored_for_matching():
    base = dialogue(n=300, seed=11)
    tgt = tgt_cues(base, warp=lambda t: t + 5)
    for c in tgt[::10]:
        c[2] = "[MUSIC]"
    out, st = run(tgt, ref_cues(base))
    assert st["embeddable"] == len(tgt) - len(tgt[::10])
    assert accuracy(out, base) >= 0.95


# ---------- realistic text hazards ----------

def test_repeated_short_replies():
    """'Oui.' / 'Merci.' everywhere: identical vectors must not derail the chain."""
    base = dialogue(n=300, seed=12)
    words = [("Oui.", "Yes."), ("Merci.", "Thanks."), ("Non.", "No."), ("Quoi ?", "What?")]
    ref, tgt = ref_cues(base), tgt_cues(base, warp=lambda t: t + 6.0)
    for k in range(0, 300, 4):
        fr, en = words[k // 4 % 4]
        ref[k][2], tgt[k][2] = en, fr
    out, st = run(tgt, ref)
    real = [c for c in base if c[2] % 4]             # concept cues only: short replies carry no id
    assert accuracy(out, real) >= 0.95, st


def _split_sentences(base, every):
    """Reference keeps one cue per sentence; the target splits every `every`-th sentence in two
    cues (halves 'k{k}a' and 'k{k}b'), as subtitlers of two languages often do."""
    ref, tgt = [], []
    for s, e, k in base:
        if k % every == 0:
            ref.append([s, e, f"line k{k}a k{k}b k{k}"])
            m = (s + e) / 2
            tgt += [[s, m, f"cue k{k}a"], [m, e, f"cue k{k}b"]]
        else:
            ref.append([s, e, f"line k{k}"])
            tgt.append([s, e, f"cue k{k}"])
    return ref, tgt


@pytest.mark.parametrize("n_cues,shift", [(4, -1.9), (6, -1.9), (8, -1.9), (5, 2.5)])
def test_short_local_disagreement_is_not_a_cut(n_cues, shift):
    """Regression for Chernobyl S01E02: the target was in sync, but over 4 consecutive lines the
    reference was timed ~1.9 s apart (a sentence split differently). That is not a cut: no
    segment shorter than min_seg, the file stays untouched."""
    base = dialogue(n=300, seed=18)
    ref = ref_cues(base)
    for c in ref[120:120 + n_cues]:
        c[0] += shift; c[1] += shift
    tgt = tgt_cues(base)
    out, st = run(tgt, ref)
    assert out == tgt, st


def test_split_sentences_in_sync_stay_untouched():
    """Sentences split in two target cues where the reference has one."""
    base = dialogue(n=300, seed=13)
    ref, tgt = _split_sentences(base, every=5)
    out, st = run(tgt, ref)
    assert out == tgt, st


@pytest.mark.parametrize("every", [1, 2])
@pytest.mark.parametrize("warp", [lambda t: t + 8.0, lambda t: t * (1 + PAL) - 3.0], ids=["const", "fps"])
def test_heavily_split_target_keeps_its_anchors(every, warp):
    """When the target splits most sentences, each half alone often falls below min_sim: the two
    halves together must still anchor them (target-side 2-cue units)."""
    base = dialogue(n=300, seed=19)
    ref, tgt = _split_sentences(base, every=every)
    tgt = [[warp(s), warp(e), x] for s, e, x in tgt]
    out, st = run(tgt, ref)
    assert out is not None and st["coverage"] >= 0.6, st
    first_halves = [c for c in out if not c[2].endswith("b")]
    assert accuracy(first_halves, base) >= 0.95, st


def test_split_sentences_with_offset():
    base = dialogue(n=300, seed=14)
    ref, tgt = _split_sentences(base, every=5)
    tgt = [[s + 8.0, e + 8.0, x] for s, e, x in tgt]
    out, st = run(tgt, ref)
    assert st["segments"] == 1, st
    first_halves = [c for c in out if not c[2].endswith("b")]
    assert accuracy(first_halves, base, tol=0.3) >= 0.95, st


# ---------- invariants ----------

def test_output_invariants():
    base = dialogue(n=320, seed=15)
    tgt = tgt_cues(base, **CASES["cut_fps"])
    snapshot = copy.deepcopy(tgt)
    out, _ = run(tgt, ref_cues(base))
    assert tgt == snapshot                                   # input not mutated
    assert [c[2] for c in out] == [c[2] for c in tgt]        # same cues, same order, same text
    assert all(e > s for s, e, _ in out)                     # positive durations
    assert all(a[0] <= b[0] for a, b in zip(out, out[1:]))   # still sorted
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(out, out[1:]) if a[0] < b[0])   # no overlap created


def test_deterministic():
    base = dialogue(n=300, seed=16)
    tgt, ref = tgt_cues(base, **CASES["split_3"]), ref_cues(base)
    assert run(tgt, ref) == run(tgt, ref)


def test_stats_shape():
    base = dialogue(n=300, seed=17)
    _, st = run(tgt_cues(base, warp=lambda t: t + 3), ref_cues(base))
    assert st["status"] == "ok"
    assert 0.9 <= st["coverage"] <= 1.0
    assert st["segments"] == 1 and abs(st["seg"][0]["a"] + 3) < 0.05


def test_max_abs_offset_includes_drift():
    """With a frame-rate drift the largest shift is at the end of a segment, not at its start."""
    base = dialogue(n=200, seed=5)
    tgt = tgt_cues(base, warp=lambda t: t / (1 + PAL))
    out, st = run(tgt, ref_cues(base))
    largest = max(abs(o[0] - t[0]) for o, t in zip(out, tgt))
    assert abs(st["max_abs_offset"] - largest) < 0.5
