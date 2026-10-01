# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [0.9.0] - 2026-10-01

### Added
- Command line accepts a video as reference: its fullest embedded text subtitle is used
  (`--track` to pick one). Exit status `0` corrected or in sync, `1` refused, `2` error.
- `core.resync()`: alignment plus the decision (`corrected`, `in_sync`, `refused`).
- `tools/quantize_model.py`: builds the int8 model used on small CPUs.
- MIT license, contribution guide, Bazarr integration guide.

### Changed
- The engine is independent of any subtitle manager. Bazarr support moved to
  `integrations/bazarr/`.
- The output of the command line defaults to `SUBTITLE.synced.srt`.

## [0.8.0] - 2026-10-01

### Added
- Two consecutive target lines can match one reference line (a sentence split in two).
  Inspired by DuoSubs.

## [0.7.0] - 2026-10-01

### Added
- First packaged version: engine, queue worker, Bazarr hook, test suite.

### Fixed
- Subtitles with `\r\r\n` line endings lost the text of every line.
