**English** | [Français](README.fr.md)

# SemanticSubSync

Re-times a downloaded subtitle in the cases where the usual sync tools fail: a scene added or
cut, a different frame rate, or both. It compares **what is said**, line by line, with a
subtitle already in sync (usually the one embedded in the video), even in another language. When
that reference does not match, it says so and leaves the file alone instead of reporting a
success.

```bash
semantic-subsync Movie.fr.srt Movie.mkv     # reference: the subtitle embedded in the video
```

In Jellyfin, a [plugin](integrations/jellyfin/README.md) does it from the menu of a movie or an
episode, and automatically for every new subtitle from its installation on.

## Where other tools fail

One example, built for this project ([`examples/lighthouse`](examples/lighthouse)). Each tool
gets the same subtitle files, with a reference subtitle that is in sync with the video.

| Tool | Scene missing + 25 fps | Extra scene | Wrong reference (commentary track) |
|---|---|---|---|
| alass 2.0.0 | ✅ in sync | ❌ part of the file off | ❌ rewrites the file instead of leaving it alone |
| ffsubsync 0.5.1 (with or without `--split-penalty`) | ❌ half of the file off | ❌ half of the file off | ❌ rewrites the file instead of leaving it alone |
| LAPSE 2.2.3 | ❌ half of the file off, says "solid" | ❌ half of the file off, says "solid" | ❌ rewrites the file and says "solid" |
| **SemanticSubSync** | ✅ **in sync** | ✅ **in sync** | ✅ **unsure, file left alone** |

This describes these files only, not every video. The figures, the details and the scripts to
rerun it are in the example folder.

Two failures come back in this example and in the [benchmark](#results):

- **Cuts and inserted scenes.** Aligning on the audio or on the timing pattern of another
  subtitle handles an offset or a frame-rate change; after a scene missing or added, part of the
  file often stays off. SemanticSubSync knows which line is which, so it finds where the cut is
  and moves each part by its own offset.
- **Failures reported as success.** A file left off by a minute, or re-timed on a commentary
  track, comes out as a success. SemanticSubSync counts the lines it could match: below 25 %, it
  is **unsure** and writes nothing.

On the benchmark (105 distorted embedded tracks), SemanticSubSync passed 104 cases; alass passed
84 and had 10 gross failures, none of them reported.

## Why this project exists

Subtitles downloaded for a movie or an episode are often made for another release of the same
video:

- another frame rate (23.976 vs 25 fps);
- a scene added or cut;
- a different intro.

The result is a subtitle that drifts, or that is fine for 20 minutes and then off by 4 seconds.

Yet most videos already carry a perfectly timed subtitle: the embedded track, often in the
original language.

SemanticSubSync uses it as a reference and matches lines **by meaning**, with a small
multilingual sentence model: "Where did you put the keys?" and "Où as-tu mis les clés ?" are
recognized as the same line.

When too few lines match (wrong reference, commentary track, different cut), it is **unsure** and
leaves the file alone.

## Example

A French subtitle was downloaded for a 10-minute episode (an
[original dialogue](examples/lighthouse) written for this project).

- It was timed on a TV broadcast: 25 fps instead of the video's 23.976, and one 70-second scene
  missing.
- The video carries an English subtitle, in sync.

```console
$ semantic-subsync downloaded.fr.srt reference.en.srt
corrected -> downloaded.fr.synced.srt (2 segment(s), largest shift +90.69 s) [reference: reference.en.srt]
```

| Line | Heard in the video at | Before | After |
|---|---|---|---|
| Il y a quelqu'un là-haut ? | 0:20.0 | 0:19.2 (0.8 s early) | 0:20.0 ✅ |
| Au crochet près de la porte. Pourquoi ? | 4:18.9 | 4:08.3 (10.6 s early) | 4:18.9 ✅ |
| *the missing scene* | 4:45 to 5:55 | | |
| Encore. Plus fort cette fois. | 6:18.7 | 4:55.9 (82.8 s early) | 6:18.7 ✅ |
| Oui ? | 9:26.5 | 7:56.0 (90.5 s early) | 9:26.4 ✅ |

## Scope

### Good fit

- The video has an embedded **text** subtitle (SRT, ASS, WebVTT, mov_text), in any language.
- Or you have another subtitle file that you know is in sync with your video.
- The subtitle to fix is a regular `.srt`.

### Not a fit

- No reference at all: this tool does not listen to the audio. Use ffsubsync or alass.
- Bitmap-only embedded subtitles (PGS, VobSub): they would need OCR, which is out of scope.
- A reference that comes from another release than your video (e.g. a downloaded English
  subtitle): it has the same timing problems as the file you want to fix.

## How it works

What it corrects, one case at a time, on 40 seconds of dialogue. This is a schematic: one mark
per line, blue in sync, orange out of sync, green fixed.

![Offset: every French line is 3 s late and is moved back by 3 s](docs/fix-offset.svg)

![Frame rate: the French drifts more and more and is stretched back](docs/fix-frame-rate.svg)

![Lines missing from the French: the French after the gap is moved later](docs/fix-missing-lines.svg)

![Extra lines in the French: the French after them is moved earlier and the extra lines are left out](docs/fix-extra-lines.svg)

![Wrong reference: no line of the commentary matches the French, the file is left as it is](docs/fix-wrong-reference.svg)

### Steps

1. **Embedding.** Every line is cleaned (tags, hearing-impaired annotations, speaker names) and
   turned into a vector by a multilingual sentence model, run locally on CPU (see
   [Model](#model)).
2. **Candidates.** Each line, alone or merged with the next one (a sentence split in two), is
   compared with the reference lines, alone or merged by two. The 3 best candidates above a
   similarity threshold are kept.
3. **Anchors.** A weighted longest increasing chain keeps the candidates that respect the order
   of the dialogue. Anchors whose offset disagrees with their neighbours are dropped.
4. **Timeline.** One frame-rate ratio is chosen for the whole file among the standard ones
   (23.976 / 24 / 25 / 29.97 / 30). The timeline is then split into constant-offset segments,
   which absorbs cuts and inserted scenes.
5. **Extra lines.** Some lines have no room in the video:
   - at a cut, a line that would land where the reference says nothing belongs to a scene the
     video does not have;
   - a line that would start before 0 s is typically a "Previously on" recap that the video
     lacks.

   By default they are all removed. With `--extra-lines keep`, they are kept only where
   nothing is shown nor said, consecutive ones as a block, whole or not at all: a translator's
   credit stays, a recap or a whole scene goes. They never overlap another line.

   The result is always in time order.

### Guards

- Fewer than 25 % of lines anchored: **unsure**, the file is left alone: the reference does not
  say the same thing.
- Every correction smaller than 0.5 s: the file is **left untouched**. This is the natural gap
  between two languages, not a sync problem.
- Lines that overlap in the file itself (a watermark, two speakers) are not a reason to rewrite
  it, and a correction keeps them.
- Segments shorter than 60 s are treated as local mismatches, not cuts.

No AI service, no network access once the model is downloaded, and the same input always gives
the same output.

## Results

These figures describe the benchmark and the datasets below. They show how the tool behaved on
those files, not what it will do on any video: on other files, other languages or other
hardware, they will differ.

They were measured with the minilm model. The static model, the default since 0.11, was run
again on the same benchmark and on the same real-world datasets: it passed as many benchmark
cases and made the same decisions on the datasets (see [Model](#model)).

### Benchmark on embedded tracks

- **Videos:** 15 that carry both a French and an original-language embedded subtitle.
- **Distortions:** the French track is distorted in 7 realistic ways: constant offset,
  frame-rate change in both directions, 3 cuts, inserted and removed scenes, cuts plus
  frame-rate change.
- **Task:** re-sync it on the original-language track.
- **Pass:** at least 95 % of lines start within 300 ms of their true position.

| Tool (embedded subtitle as reference) | Cases passed | Gross failures (< 80 % of lines) |
|---|---|---|
| **SemanticSubSync** (0.7) | **104 / 105** | 1 |
| alass | 84 / 105 | 10, none reported |

**Invalid cases.** On 11 cases with a commentary track or a partial track as reference,
SemanticSubSync left alone (unsure) the ones where it would have done damage:

- the coverage of anchored lines was 0.07 or less there, against 0.32 to 0.82 on the valid
  cases;
- the threshold (0.25) sits in that gap; another library may need another value
  (`--min-coverage`).

**Audio-only tools,** for comparison, on the same kind of distortions: ffsubsync 29 / 44,
alass 24 / 44, subaligner 2 / 44, with no confidence signal on failures.

**Speed** on a NAS with a 2-core Celeron J4025, for a full movie (9.7 GB video, 1528 lines):

| Model (see [Model](#model)) | Time | RAM at peak |
|---|---|---|
| static | about 97 s | 680 MB |
| int8 minilm | about 175 s | 620 MB |

With static, most of the time goes into reading the embedded subtitle out of the video (ffmpeg).
It depends on the hardware, the size of the video and the number of lines.

### Real-world datasets

The benchmark above starts from correct tracks. To see the behaviour on files as they are found
online, the tool was also run on real-world datasets:

- 66 subtitles downloaded from a public subtitle site, for 8 TV episodes;
- 16 languages: Arabic, Chinese, English, French, German, Greek, Hungarian, Italian, Japanese,
  Polish, Portuguese (Portugal and Brazil), Russian, Spanish, Swedish, Turkish;
- each one aligned on the English subtitle of its episode, then the English one on it: 114
  pairs.

These files are not in the repository. [`tools/bench_real.py`](tools/bench_real.py) runs the
same measurement on any such folder.

There is no ground truth here, so the measure is a proxy. The lines that have **one** obvious
translation in the other file (similarity 0.70 or more, 0.15 above any other line) should start
within 0.5 s / 1 s of it. Two translations are rarely cut the same way, so even a pair in sync
stays below 100 %.

| Decision | Pairs | Lines within 0.5 s / 1 s of their translation (median) |
|---|---|---|
| Left untouched (already in sync) | 66 | 92.8 % / 97.4 % |
| Corrected | 44 | before: 1.9 % / 4.5 % — after: 82.7 % / 91.5 % |
| Unsure, left alone | 4 | — |

- **Corrected pairs** covered the cases the tool is made for: 25 fps versus 23.976 fps (3
  episodes), cuts and scenes added or removed (3 to 6 segments, including an extended DVD cut),
  a 90 s recap present in only one release.
  - 28 of the 44 reached 90 % or more at 1 s.
  - The lowest was 69 % (a Portuguese translation cut very differently from the English one).
  - 2 of the 44 ended slightly below the untouched file (one Greek subtitle in both
    directions, 95.7 % then 93.2 % at 1 s), for a correction of about 0.5 s, just above the
    deadband.
- **Unsure:** 2 files in both directions, whose content belongs to another episode than their
  name says (the dialogue and the duration do not match the reference).
- **Extra lines:** 170 had no room in the video, 140 of them being the 35-line recap of one
  release, aligned on releases without it.
  - Measured with `--extra-lines keep`: 9 isolated ones stayed in silences, 161 were left out.
  - The default (`drop`) removes all 170. Before this, they would have been stacked at
    00:00:00.
- **Invariants:** no line came out of time order or before 0 s, and no overlap longer than
  0.3 s was created.
- **Encodings:** 47 of the 66 files were not UTF-8 (see [Usage](#usage)).

This is not a published benchmark. The test suite reproduces each distortion on synthetic
dialogue (see [Development](#development)).

## Installation

- Python 3.11 or later, on 64-bit Linux, macOS or Windows.
- ffmpeg / ffprobe, only when the reference is a video.
- ARM64 (Raspberry Pi class hardware) is covered by the CI: tests, both models, the example
  and the Docker image run on an ARM64 machine, with the same results as on x86-64.
- A Raspberry Pi needs a 64-bit OS: the ONNX runtime has no 32-bit ARM build.

```bash
pip install "semantic-subsync[model] @ git+https://github.com/ludosch/SemanticSubSync"
```

The default model (about 220 MB) is downloaded from Hugging Face on first use.

Each [release](https://github.com/ludosch/SemanticSubSync/releases) also carries the Python
package and a Docker image for amd64 and arm64, to load with `docker load`:

```bash
gh release download v0.11.0 -R ludosch/SemanticSubSync -p "*docker-amd64*"
docker load -i semantic-subsync-0.11.0-docker-amd64.tar.gz
```

## Usage

```bash
semantic-subsync SUBTITLE REFERENCE [-o OUTPUT] [--track INDEX] [--lang CODE]
                 [--extra-lines drop|keep] [--min-coverage X] [--json]
```

**Reference.** `REFERENCE` is a `.srt` in sync with the video, or the video itself. With a
video, the fullest embedded text subtitle is used, in any language, forced tracks excluded.
`--track` picks a stream by its ffprobe index instead.

**Output.**

- The input subtitle is never modified. The result goes to `SUBTITLE.synced.srt` by default
  (`-o` to change it).
- Already in sync: nothing is written.
- Exit status: `0` corrected or already in sync, `1` unsure (nothing written), `2` error.
- `--json` prints the decision and the statistics (coverage, segments, offsets, lines dropped,
  extra lines kept).

**Extra lines.** The lines the video has no room for are removed (see
[How it works](#how-it-works)). `--extra-lines keep` leaves them where nothing is shown nor
said.

**Encodings.** Files that are not UTF-8 (most older downloads) are read in the code page of
their language:

- taken from the file name (`Movie.ru.srt`, `Show.S01E01.pt-BR.srt`) or from `--lang ru`;
- without either, a Western code page is assumed, unless the text then looks like another
  script, in which case the encoding is guessed.

**From Python:**

```python
from semantic_subsync import core, media

status, cues, stats = core.resync(media.read_srt("Movie.fr.srt"), core.parse("Movie.en.srt"))
if status == "corrected":
    core.write("Movie.fr.synced.srt", cues)
```

## Integrations

The engine knows nothing about media servers or subtitle managers. Integrations live in
[`integrations/`](integrations):

- [**Bazarr**](integrations/bazarr/README.md): each downloaded subtitle is checked
  automatically by a background worker and corrected when needed. The downloaded version stays
  available as an extra track, and every decision is logged.
- [**Jellyfin**](integrations/jellyfin/README.md): a plugin that installs the engine itself. "Sync
  subtitles" in the menu of a movie or an episode re-times its subtitles; from the installation on,
  new videos and new subtitle files are handled automatically (the ones already there on request).
  The item is refreshed so the tracks show at once. No extra container; Jellyfin needs write access
  to the media folders.

## Model

Two multilingual sentence models, both published by
[sentence-transformers](https://www.sbert.net/) under the Apache 2.0 license:

| Name | Model | |
|---|---|---|
| `static` (default) | [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1), its official float16 export, first 512 dimensions | Averaged word vectors, no neural network to run: on the benchmark, its vectors took about 1 % of minilm's time (see [Speed](#benchmark-on-embedded-tracks) for whole files) |
| `minilm` | [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), through [fastembed](https://github.com/qdrant/fastembed) | A small transformer: slower, a little better on some hard cases |

On the benchmark and the real-world datasets the two made the same decisions; minilm corrected
a few hard files better (a frame-rate change on a different cut, for instance). Choose the model with
`--model`, `SEMSYNC_MODEL`, or, for one subtitle in the worker, `one --model` (see the
[Bazarr guide](integrations/bazarr/README.md#test-one-pair-by-hand)). Each model has its own
similarity threshold, set in the code.

**static in float16.** The model is read from the official float16 export, half the download of
the float32 file. On the benchmark and the real-world datasets it made the same decisions as the
float32 file; the similarities differed by 0.0001 at most.

| Variable | Meaning |
|---|---|
| `SEMSYNC_MODEL` | `static` (default) or `minilm` |
| `SEMSYNC_MODEL_DIR` | Folder holding local copies, one sub-folder per model: `static/` (`tokenizer.json`, and `model_fp16.onnx` or `model.safetensors`), `minilm/` (e.g. the int8 one, see below). A model without its sub-folder is downloaded from Hugging Face on first use |
| `SEMSYNC_CACHE` | Optional folder where embeddings are cached on disk |

**int8 minilm.** [`tools/quantize_model.py`](tools/quantize_model.py) builds a 112 MB int8 copy.
On a NAS with a 2-core Celeron J4025 it was about 40 % faster than the original minilm and used
2.5 times less RAM, with the same results.

**Languages.** Both subtitles must be in languages the model was trained on. The minilm model
card lists (the static one lists the same, with zh for both Chinese variants): ar, bg, ca, cs, da, de, el, en, es, et, fa, fi, fr, fr-ca, gl, gu, he, hi, hr, hu, hy,
id, it, ja, ka, ko, ku, lt, lv, mk, mn, mr, ms, my, nb, nl, pl, pt, pt-br, ro, ru, sk, sl, sq,
sr, sv, th, tr, uk, ur, vi, zh-cn, zh-tw. Another language may partly work, untested.

## Development

```bash
mise x -- uv run pytest                                               # fast, no model needed
SEMSYNC_TEST_MODEL=1 mise x -- uv run --extra model pytest -m model   # end to end with each real model
SEMSYNC_CORPUS=~/corpus SEMSYNC_CACHE=~/corpus/emb mise x -- uv run --extra model pytest -m corpus   # local real subtitles
```

**Unit tests** run on **synthetic** dialogue (`tests/synth.py`):

- each line carries a "concept" token such as `k17`, which a deterministic fake model turns
  into a fixed vector;
- the two languages of one concept score about 0.9, unrelated lines about 0.1, like the real
  model;
- this tests the algorithm independently of the model, against every distortion of the
  benchmark. No film extract is stored in the repository.

**Corpus tests** (`-m corpus`) run every pair of a local folder of real subtitles (see
[Real-world datasets](#real-world-datasets)). They check the invariants, and that no correction
is clearly worse than the untouched file. They are skipped when `SEMSYNC_CORPUS` is not set.

**CI** runs on pushes to `main` that change code, not on documentation-only changes.

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [changelog](CHANGELOG.md).

## Acknowledgements

- [ffsubsync](https://github.com/smacke/ffsubsync) and [alass](https://github.com/kaegi/alass),
  the reference tools this project was measured against.
- [DuoSubs](https://github.com/CK-Explorer/DuoSubs), whose sentence-level approach inspired the
  matching of split sentences.

## License

[MIT](LICENSE)
