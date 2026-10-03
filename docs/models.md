# Models

[README](../README.md) · [How it works](how-it-works.md) · [Results](results.md) · [Usage](usage.md) · **Models**

Two multilingual sentence models, both published by
[sentence-transformers](https://www.sbert.net/) under the Apache 2.0 license:

| Name | Model | |
|---|---|---|
| `static` (default) | [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1), its official float16 export, first 512 dimensions | Averaged word vectors, no neural network to run: on the benchmark, its vectors took about 1 % of minilm's time (see [Speed](results.md#speed) for whole files) |
| `minilm` | [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), through [fastembed](https://github.com/qdrant/fastembed) | A small transformer: slower, a little better on some hard cases |

On the benchmark and the real-world datasets the two made the same decisions; minilm corrected
a few hard files better (a frame-rate change on a different cut, for instance). Choose the model
with `--model`, `SEMSYNC_MODEL`, or, for one subtitle in the worker, `one --model` (see the
[Bazarr guide](../integrations/bazarr/README.md#test-one-pair-by-hand)). Each model has its own
similarity threshold, set in the code.

**static in float16.** The model is read from the official float16 export, half the download of
the float32 file. On the benchmark and the real-world datasets it made the same decisions as the
float32 file; the similarities differed by 0.0001 at most.

| Variable | Meaning |
|---|---|
| `SEMSYNC_MODEL` | `static` (default) or `minilm` |
| `SEMSYNC_MODEL_DIR` | Folder holding local copies, one sub-folder per model: `static/` (`tokenizer.json`, and `model_fp16.onnx` or `model.safetensors`), `minilm/` (e.g. the int8 one, see below). A model without its sub-folder is downloaded from Hugging Face on first use |
| `SEMSYNC_CACHE` | Optional folder where embeddings are cached on disk |

**int8 minilm.** [`tools/quantize_model.py`](../tools/quantize_model.py) builds a 112 MB int8
copy. On a NAS with a 2-core Celeron J4025 it was about 40 % faster than the original minilm and
used 2.5 times less RAM, with the same results.

## Languages

Both subtitles must be in languages the model was trained on. The minilm model card lists (the
static one lists the same, with zh for both Chinese variants): ar, bg, ca, cs, da, de, el, en,
es, et, fa, fi, fr, fr-ca, gl, gu, he, hi, hr, hu, hy, id, it, ja, ka, ko, ku, lt, lv, mk, mn,
mr, ms, my, nb, nl, pl, pt, pt-br, ro, ru, sk, sl, sq, sr, sv, th, tr, uk, ur, vi, zh-cn, zh-tw.
Another language may partly work, untested.
