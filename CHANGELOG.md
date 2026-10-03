# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Option to hide the original download: with `SEMSYNC_KEEP_DOWNLOAD=hidden` (Jellyfin plugin: "Hide
  the original download"), a corrected subtitle's download is kept as `Movie.fr.srt.orig`, which
  players ignore, so only the correction is listed. Off by default.
- Jellyfin plugin: a logo in the plugin catalog.
- Each release carries `semantic-subsync-X.Y.Z-constraints.txt`: every dependency of the engine,
  pinned as tested. The Jellyfin plugin installs the engine with it.
- Worker: `SEMSYNC_MEDIA_ROOT`, the folders queued jobs must be in (default: anywhere).
- `semantic_subsync.read_srt` (any encoding), `core.similarity`, and `core.unload()` to free the
  models and the embedding cache.

### Changed
- `pip install semantic-subsync` runs the default `static` model; the `static` extra is no longer
  needed (kept, empty), `model` adds the `minilm` runtime.
- The README's `pip install` command installs the latest release instead of the development
  branch.
- Engine settings are checked: `core.P` is read-only, `core.params()` and the worker's `SEMSYNC_*`
  variables reject unknown or invalid values with a clear message.
- Worker: `unchanged` jobs are no longer logged one by one (one summary line per batch);
  tracebacks go to the log file only.
- Docker image: runs as uid/gid 1000, with default `SEMSYNC_DIR` and `/models` paths, and installs
  dependencies pinned with hashes.
- Jellyfin plugin: an engine update is built aside and replaces the engine only once it works; a
  failed update keeps the previous one.
- Jellyfin plugin: "Sync subtitles" shows only in the item's own menu and closes it cleanly; a
  second click follows the sync already running; syncs stop when Jellyfin stops.

### Fixed
- The correction is now the subtitle Jellyfin plays by default in every language. The download kept
  beside it was listed first for languages sorting after "replaced" (Russian, Swedish, Chinese...)
  and played instead; it is now named `Movie.ru.untouched.srt` (track "untouched"). Files named by
  earlier versions are renamed the next time their subtitle is checked, without a new sync.
- Worker: a downloaded subtitle is never deleted. After `state.db` is lost the `.replaced` file is
  taken as the download, and a file it cannot account for is kept aside as `.bak`.
- Worker: a job interrupted at any point (container stopped, power cut) is completed by the next
  run instead of taking its own correction for a new download.
- Worker: a subtitle replaced by Bazarr while it is being processed is left alone
  (`changed_during_run`) and processed as a new file.
- Worker: a `state.db` from 0.10 works again (`refused` reads as `unsure`); `backfill` no longer
  overwrites queued jobs and pairs a subtitle with the longest matching video name; titles that
  contain "replaced" or "resync" are processed; a broken job file no longer stops the queue.
- Command line: a crash (ffmpeg missing, failed model download, bad `SEMSYNC_MODEL`) exits with
  status 2 and one message, not a traceback with status 1 ("unsure").
- A corrupt or unreadable video is reported as an ffprobe/ffmpeg error, not as "no usable embedded
  text subtitle".
- Timings with a short fraction (`00:00:01,5`) or without hours (`01:02.500`) are read correctly.
- An `.ass` or `.vtt` file, `--track` with an `.srt` reference, or `-o` pointing at the reference is
  refused with a clear message; an explicit `--track` is used even when short.
- Lines at a cut are placed correctly next to a long reference line or a cue sharing a start time.
- Output subtitles and the embedding cache are written atomically, with the same line ends on
  every platform.
- Jellyfin plugin: the first scan of a new library no longer processes the subtitles already in
  it when catch-up is off.
- Jellyfin plugin: a subtitle that takes over 30 minutes is stopped and retried at most 3 times
  until the file changes, instead of stopping the run; an unavailable engine is retried an hour
  later instead of in a loop.
- Jellyfin plugin: a damaged history file no longer makes every existing subtitle processed;
  deleted subtitles are forgotten.
- Jellyfin plugin: the settings page no longer risks saving every library unticked while it
  loads, and reports load and save errors.
- Releases: the GitHub release is published, and becomes the one Jellyfin reads, only once every
  file is attached; a failed release run can be re-run.

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
