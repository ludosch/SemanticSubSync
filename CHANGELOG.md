# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Worker: `history TEXT` prints every logged decision about the paths containing TEXT.
- Worker: the log is limited to `SEMSYNC_LOG_MAX_MB` (default 10); beyond it, its oldest entries
  are deleted.
- `tools/smoke_image.py`: the release checks that each image reads an embedded subtitle.

### Changed
- Worker: each log line also holds the hashes of the synced subtitle and of the correction, and
  the settings. `state.db` also keeps `seg`.
- CI: one job per architecture (x86-64 on Python 3.11, ARM64 on 3.12), no Docker build on
  pushes (the release builds and checks the images), not run for documentation-only changes;
  actions updated to their Node 24 versions.
- README: shorter paragraphs, lists and sub-headings.

### Fixed
- Worker: the offsets applied per segment (`seg`) were missing from the log since 0.10.0.

## [0.10.0] - 2026-10-02

### Added
- Worker: `state.db` (SQLite) remembers every downloaded subtitle it checked (hashes, video size
  and date, decision, output). A subtitle already processed is skipped (`unchanged`) unless its
  content, the video or the output mode changed; `one --force` checks it again. `status` lists
  what it knows (`--fields` describes the columns).
- Worker: a downloaded subtitle is skipped (`redundant`) when the video already embeds a text
  subtitle of the same language and kind (plain, hearing impaired, forced).
- `--lang` and reading of non-UTF-8 subtitles in the code page of their language (from the
  file name or `--lang`); Cyrillic, Greek, Arabic or Chinese files were read as gibberish.
- `tools/bench_real.py` and `pytest -m corpus`: measurement on a local folder of real
  downloaded subtitles; README section "Real-world datasets" with the author's figures.
- README: the languages of the model.
- `--extra-lines drop|keep` (worker: `SEMSYNC_EXTRA_LINES`) for the lines the video has no room
  for (a credit, a recap or a scene it lacks). `drop`, the default, removes them all; `keep`
  leaves them only where nothing is shown nor said, consecutive lines as a block, whole or not
  at all. `--json` reports `kept_extra`.

### Changed
- Worker: by default (`SEMSYNC_OUTPUT=replace`) a corrected subtitle keeps the name of the
  download, which is kept as `<video>.replaced.<lang>.srt`, an extra track in media servers.
  `SEMSYNC_OUTPUT=side` writes the correction as `<video>.resync.<lang>.srt` instead. Files
  named `.semsync.` by earlier versions are removed when their subtitle is checked again.
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
- `tools/bench_real.py` crashed when printing its summary (numpy integers).
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
