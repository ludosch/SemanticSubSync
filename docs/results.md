# Results

[README](../README.md) · [How it works](how-it-works.md) · **Results** · [Usage](usage.md) · [Models](models.md)

These figures describe the benchmark and the datasets below. They show how the tool behaved on
those files, not what it will do on any video: on other files, other languages or other
hardware, they will differ.

They were measured with the minilm model. The static model, the default since 0.11, was run
again on the same benchmark and on the same real-world datasets: it passed as many benchmark
cases and made the same decisions on the datasets (see [Models](models.md)).

## Benchmark on embedded tracks

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

## Speed

On a NAS with a 2-core Celeron J4025, for a full movie (9.7 GB video, 1528 lines):

| Model (see [Models](models.md)) | Time | RAM at peak |
|---|---|---|
| static | about 97 s | 680 MB |
| int8 minilm | about 175 s | 620 MB |

With static, most of the time goes into reading the embedded subtitle out of the video (ffmpeg).
It depends on the hardware, the size of the video and the number of lines.

## Real-world datasets

The benchmark above starts from correct tracks. To see the behaviour on files as they are found
online, the tool was also run on real-world datasets:

- 66 subtitles downloaded from a public subtitle site, for 8 TV episodes;
- 16 languages: Arabic, Chinese, English, French, German, Greek, Hungarian, Italian, Japanese,
  Polish, Portuguese (Portugal and Brazil), Russian, Spanish, Swedish, Turkish;
- each one aligned on the English subtitle of its episode, then the English one on it: 114
  pairs.

These files are not in the repository. [`tools/bench_real.py`](../tools/bench_real.py) runs the
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
- **Encodings:** 47 of the 66 files were not UTF-8 (see [Usage](usage.md#encodings)).

This is not a published benchmark. The test suite reproduces each distortion on synthetic
dialogue (see [CONTRIBUTING.md](../CONTRIBUTING.md#tests)).
