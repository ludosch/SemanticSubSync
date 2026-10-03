# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Jellyfin plugin: a logo in the plugin catalog.

## [0.12.0] - 2026-10-03

### Added
- Jellyfin plugin (`integrations/jellyfin`, Jellyfin 12.1, Linux x86-64 and ARM64): installs
  the engine in its own data folder (uv, a standalone Python, `semantic-subsync[static]`), runs
  the worker on the subtitles of each video added to the library and, with a scheduled task, on
  the subtitle files added or changed since the last run, in every library from the installation
  on (a library can be left out on its page); never while someone is watching; then refreshes the
  item so the tracks show without a library scan. "Sync subtitles" in the menu of a movie or an
  episode runs it at once. The subtitles already there at the installation are left alone, unless
  the catch-up option is ticked (at any time). By default the correction replaces the subtitle and
  the download is kept as `replaced`; writing it next to the download, flagged as default, is an
  option.
  The engine and its model (about 410 MB) are installed in the background a few minutes after
  Jellyfin starts, one installation at a time (a sync started meanwhile waits for it); the plugin
  page shows whether the engine is ready.
  Each release carries the plugin (`jellyfin-plugin-semanticsubsync-X.Y.Z.zip`) and the plugin
  repository manifest: add
  `https://github.com/ludosch/SemanticSubSync/releases/latest/download/manifest.json` under
  Dashboard > Plugins > Repositories.
- Optional dependency set `static` (`pip install "semantic-subsync[static]"`): only what the
  default `static` model needs, without the `minilm` runtime.
- Worker `prepare`: downloads and loads the sentence model now, so the first subtitle does not wait
  for it.

### Changed
- Worker, `side` output: the correction is `<video>.resync.<lang...>.default.srt`; the "default"
  flag makes Jellyfin play it rather than the download (a forced subtitle's correction is not
  flagged).
- The `static` model is downloaded as its official float16 export (`onnx/model_fp16.onnx`, at a
  fixed revision of the model repository): about 220 MB instead of 434 MB, and half the memory for
  its table. Same decisions as the float32 file on the benchmark and the real-world datasets.
  It is downloaded directly and checked against its SHA-256, into `$HF_HOME/semantic-subsync`;
  `huggingface-hub` is no longer a direct dependency. A local copy (`SEMSYNC_MODEL_DIR/static`)
  can hold `model_fp16.onnx` or, as before, `model.safetensors`.

### Fixed
- Worker: runs where `os.nice` does not exist (Windows) instead of failing at start.
- Worker: a correction that was deleted, edited or renamed is written again; it used to be
  reported as `unchanged` and stay missing.

## [0.11.0] - 2026-10-02

### Added
- A second, much faster sentence model, `static`
  (static-similarity-mrl-multilingual-v1, first 512 dimensions), now the default; `minilm`
  stays available. Choose with `--model`, `SEMSYNC_MODEL`, or for one subtitle with
  `worker one --model NAME`: that choice is kept in `state.db` (`model`, `model_choice`) for
  later runs of the subtitle; `--model default` drops it. Each model carries its own similarity
  threshold (`core.MODELS`).
- Worker: `history WORD...` prints every logged decision about the subtitles whose path contains
  every word (e.g. `history TITLE S01E02`), grouped by subtitle with its full path; `status`
  filters the same way.
- Worker: the log is limited to `SEMSYNC_LOG_MAX_MB` (default 10); beyond it, its oldest entries
  are deleted.
- `tools/smoke_image.py`: the release checks that each image reads an embedded subtitle.
- `tools/release.py`: prepares a release (version, changelog, README download commands, tests,
  commit, tag); the release workflow refuses a tag whose version has no changelog notes.

### Changed
- The package version is read from `src/semantic_subsync/__init__.py` only.
- `SEMSYNC_MODEL_DIR` is now a folder with one sub-folder per model (`static/`, `minilm/`).
- `core.P` no longer holds `min_sim`: use `core.params(model)`. `core.sync` and `core.resync`
  take `model=`.
- The decision "refused" is now "unsure" (too few lines match the reference, the file is left
  alone): `core.resync`, logs, `state.db`, `--json`, `cli.EXIT_UNSURE` (exit status 1, unchanged)
  and the documentation.
- Worker: each log line also holds the hashes of the synced subtitle and of the correction, and
  the settings. `state.db` also keeps `seg`.
- CI: one job per architecture (x86-64 on Python 3.11, ARM64 on 3.12), no Docker build on
  pushes (the release builds and checks the images), not run for documentation-only changes;
  actions updated to their Node 24 versions.
- README: shorter paragraphs, lists and sub-headings.

### Removed
- Worker: upgrade code for earlier versions (removal of `.semsync.` side files, new columns added
  to an existing `state.db`).

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
  downloaded subtitles; README section "Real-world datasets" with the measured figures.
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
