**English** | [Français](README.fr.md)

# SemanticSubSync

Re-times a subtitle (e.g. a downloaded French one) on a reference subtitle in another language
(e.g. the original-language track embedded in the video) by matching lines **on meaning**, not on
the shape of the timing and not on the audio.

## How it works

1. Every cue is cleaned (tags, SDH, speaker names) and embedded with a multilingual sentence model
   (MiniLM, local and deterministic).
2. Candidates: target cues, single or merged by two (a sentence split in two), are matched with
   the 3 best reference cues, single or merged by two.
3. A weighted longest increasing chain gives monotonic anchors; a neighbourhood filter drops
   anchors whose offset disagrees with their neighbours.
4. One global frame-rate drift is chosen among fixed ratios (23.976 / 24 / 25 / 29.97 / 30), then
   the timeline is split into constant-offset segments (cuts, added or removed scenes).
5. Guards:
   - coverage below 0.25: refused (the reference does not say the same thing, e.g. a commentary track);
   - every correction below 0.5 s: the file is left untouched (natural bias between two languages);
   - segments shorter than 60 s are local mismatches, not cuts.

## Usage

```bash
semantic-subsync target.fr.srt reference.en.srt output.srt     # JSON stats on stdout
```

### Worker

`semantic-subsync-worker run` processes the `/data/.semsync/queue` folder, fed by Bazarr's custom
post-processing (`bazarr/enqueue.py`). For each downloaded subtitle it extracts the embedded text
subtitles of the video (forced tracks excluded), takes the fullest one as reference, and writes
`<video>.semsync.<lang>.srt` next to the video when a correction is needed. The downloaded file is
never modified.

| Variable | Meaning |
|---|---|
| `SEMSYNC_MODEL_DIR` | Local copy of the model (e.g. the int8-quantized one) |
| `SEMSYNC_DIR` | Queue, failed jobs and log folder (default `/data/.semsync`) |
| `SEMSYNC_CACHE` | Optional on-disk embedding cache |

## Development

```bash
mise x -- uv run pytest                                    # fast, no model needed
SEMSYNC_TEST_MODEL=1 SEMSYNC_MODEL_DIR=/path/to/minilm-int8g \
  mise x -- uv run --extra model pytest -m model           # end to end with the real model
```

Unit tests run on **synthetic** dialogues (`tests/synth.py`). Each line carries a "concept" token
such as `k17`, which a deterministic fake embedder turns into a fixed vector: the two languages of
one concept score ~0.9, unrelated lines ~0.1. This tests the algorithm independently of the
model, against every distortion of the benchmark: offset, frame rate, cuts, extra scenes, sentences
split differently, repeated short replies, unrelated reference. No film extract is versioned.
