"""Real downloaded subtitles (see tools/bench_real.py). The corpus is copyrighted and stays local:
these tests are skipped unless SEMSYNC_CORPUS points to it. Embeddings come from the real model,
or from SEMSYNC_CACHE once computed, so a second run takes seconds.

Run: SEMSYNC_CORPUS=~/corpus SEMSYNC_CACHE=~/corpus/emb uv run --extra model pytest -m corpus
"""
import importlib.util, os, pathlib

import pytest

CORPUS = os.environ.get("SEMSYNC_CORPUS")
pytestmark = [pytest.mark.corpus,
              pytest.mark.skipif(not CORPUS, reason="SEMSYNC_CORPUS not set (local real-subtitle corpus)")]

_spec = importlib.util.spec_from_file_location(
    "bench_real", pathlib.Path(__file__).parent.parent / "tools" / "bench_real.py")
bench = importlib.util.module_from_spec(_spec)
if CORPUS:
    _spec.loader.exec_module(bench)
PAIRS = list(bench.pairs(CORPUS)) if CORPUS else []


@pytest.mark.parametrize("ep,tgt,ref", PAIRS, ids=[f"{e}:{t['lang']}<-{r['lang']}" for e, t, r in PAIRS])
def test_real_pair(ep, tgt, ref):
    rec = bench.measure(CORPUS, ep, tgt, ref)
    assert not (rec.get("unordered") or rec.get("before_zero") or rec.get("overlaps_created")), rec
    if rec["status"] == "corrected":
        # never clearly worse than the untouched file (proxy accuracy at 1 s)
        assert rec["after"][1] >= rec["before"][1] - 0.05, rec
