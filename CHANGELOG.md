# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `--lang` and reading of non-UTF-8 subtitles in the code page of their language (from the
  file name or `--lang`); Cyrillic, Greek, Arabic or Chinese files were read as gibberish.
- `tools/bench_real.py` and `pytest -m corpus`: measurement on a local folder of real
  downloaded subtitles; README section "Real-world datasets" with the author's figures.
- README: the languages of the model.
- `--extra-lines keep|drop` (worker: `SEMSYNC_EXTRA_LINES`) for the lines the video has no room
  for (a credit, a recap or a scene it lacks). `keep`, the default, leaves them only where
  nothing is shown nor said, consecutive lines as a block, whole or not at all; `drop` removes
  them all. `--json` reports `kept_extra`.

### Changed
- Lines of a scene that the video does not have are no longer stacked on the lines around
  the cut, and lines that would start before 0 s (a "Previously on" recap the video lacks) are
  no longer written at 00:00:00: see `--extra-lines`. `--json` reports how many lines were
  dropped in `dropped`.
- The output is always in time order.
- New dependency: `charset-normalizer`, to guess the encoding of a non-UTF-8 file whose
  language is unknown.
- README: one schematic per case (offset, frame rate, missing lines, extra lines, wrong
  reference), drawn by `examples/lighthouse/plot.py`.

### Fixed
- A subtitle in sync whose own lines overlap (e.g. a watermark) was reported as corrected and
  rewritten. Only overlaps created by a correction are trimmed now.

## [0.9.0] - 2026-10-01

### Added
- Command line accepts a video as reference: its fullest embedded text subtitle is used
  (`--track` to pick one). Exit status `0` corrected or in sync, `1` refused, `2` error.
- `core.resync()`: alignment plus the decision (`corrected`, `in_sync`, `refused`).
- `tools/quantize_model.py`: builds the int8 model used on small CPUs.
- MIT license, contribution guide, Bazarr integration guide.
- Releases carry the Python package and a Docker image for amd64 and arm64, built by the CI.
- `examples/lighthouse`: an original dialogue in three situations (scene missing, extra scene,
  wrong reference), with the scripts that compare SemanticSubSync with alass, ffsubsync and LAPSE.

### Changed
- The engine is independent of any subtitle manager. Bazarr support moved to
  `integrations/bazarr/`.
- The output of the command line defaults to `SUBTITLE.synced.srt`.

### Fixed
- `max_abs_offset` ignored the frame-rate drift inside a segment and under-reported the largest
  shift.

## [0.8.0] - 2026-10-01

### Added
- Two consecutive target lines can match one reference line (a sentence split in two).
  Inspired by DuoSubs.

## [0.7.0] - 2026-10-01

### Added
- First packaged version: engine, queue worker, Bazarr hook, test suite.

### Fixed
- Subtitles with `\r\r\n` line endings lost the text of every line.
