# Bazarr integration

Every subtitle that [Bazarr](https://www.bazarr.media/) downloads is checked in the background
against the text subtitle embedded in the video. When it is out of sync, it is corrected, and
the downloaded version is kept beside it as an extra track to switch back to.

```
Bazarr ──(custom post-processing)──> enqueue.py ──> /data/.semsync/queue/*.job
                                                          │
                              semantic-subsync-worker <───┘
                                          │
                 Movie.fr.srt            corrected (keeps the name Bazarr gave it)
                 Movie.fr.untouched.srt  the download, kept only when a correction was made
```

Bazarr has no plugin system for synchronization engines: its built-in sync (ffsubsync) is
hard-wired. Its **custom post-processing** command is the supported extension point, so this
integration uses it. The command only drops a small job file; the work runs in a separate
container, so Bazarr is never slowed down and does not need the model.

## What the player shows

Jellyfin (and most media servers) read the words between the video's name and `.srt`: the
language, `hi` / `sdh` / `cc` for hearing impaired, `forced`, and any other word as the title of
the track. With the default output mode:

| File | Shown in Jellyfin |
|---|---|
| `Movie.fr.srt` | French - SUBRIP - External (the correction, played by default) |
| `Movie.fr.untouched.srt` | untouched - French - SUBRIP - External |
| `Movie.fr.hi.untouched.srt` | untouched - French - Hearing impaired - SUBRIP - External |

For a language, Jellyfin plays the first external subtitle in file name order: `untouched` comes
last in the name so that the correction is listed first in every language. If a correction is
wrong, pick the "untouched" track. With `SEMSYNC_KEEP_DOWNLOAD=hidden` the download is kept as
`Movie.fr.srt.orig` instead, a name that Jellyfin and Bazarr do not read: only the correction
shows. `SEMSYNC_OUTPUT=side` keeps the download under its own name instead and writes the
correction as `Movie.resync.fr.default.srt` ("resync - French - Default"): the "default" flag
makes Jellyfin play the correction first. A media server may only see a new file after its next
library scan.

When Bazarr downloads a new subtitle over a corrected one (an upgrade, a manual search), the
new file is checked again and the old kept download is replaced or removed. When a subtitle
needs no correction (any more), it keeps its name and no extra file is left. A download kept by
an earlier version (`Movie.replaced.fr.srt`) or under the other `SEMSYNC_KEEP_DOWNLOAD` value is
renamed the next time its subtitle is checked (`backfill` checks them all), without a new sync.

A downloaded subtitle is never deleted: a file whose content the worker cannot account for
(see [State](#state)) is renamed `<name>.<date>.bak` instead of being overwritten or removed.
Players and the worker ignore these files; delete them once checked.

## What is checked

- Each downloaded subtitle on its own: a video with `fr`, `fr.hi` and `en` subtitles gets three
  checks.
- A subtitle is skipped (`redundant`) when the video already embeds a **text** subtitle of the
  same language and kind (plain, hearing impaired or forced): the video has it already. An
  image subtitle (PGS, VobSub) does not count, since many players cannot show it without
  converting the video.
- The reference is the fullest embedded text subtitle, in any language: an English download on
  a release that only embeds French is checked against the French.
- A subtitle already processed is not processed again (`unchanged`) unless its content, the
  video (size or date) or the output mode changed: see [State](#state).

## Setup

### 1. Shared folder

The worker must see the media files **at the same paths as Bazarr**, plus an exchange folder.
The examples below use `/data` for both, with the exchange folder at `/data/.semsync`.

### 2. The hook

Copy [`enqueue.py`](enqueue.py) to `/data/.semsync/enqueue.py`. It has no dependency and runs
with Bazarr's own Python.

### 3. Bazarr settings

In **Settings → Subtitles**:

- **Custom post-processing**: on.
- **Command**:
  ```
  /lsiopy/bin/python3 /data/.semsync/enqueue.py {{episode}} {{subtitles}} {{score}}
  ```
  `/lsiopy/bin/python3` is the Python of the linuxserver.io image. With another image, use any
  `python3` available inside the Bazarr container. Bazarr runs the command without a shell and
  passes each `{{variable}}` as one argument, so paths with spaces are safe.
- **Post-processing thresholds** (series and movies): `100` checks every subtitle except perfect
  matches. Lower it to check only the poorer ones. The score is a poor guide to sync: it
  measures how closely the subtitle's release name matches the video's, not its timing, so a
  high score can still be off and a low one in sync.
- **Automatic subtitles synchronization** (Bazarr's own ffsubsync): turn it off, so that the
  downloaded file stays as published and the worker compares it with the original.

### 4. The worker

Build the image from the repository and run it with Docker Compose:

```yaml
services:
  semantic-subsync:
    build: https://github.com/ludosch/SemanticSubSync.git
    container_name: semantic-subsync
    user: "1000:1000"             # uid:gid owning your media files (the image's default user is 1000:1000)
    volumes:
      - /path/to/data:/data          # same mount as in the Bazarr container
      - ./models:/models             # models, kept when the container is recreated
    mem_limit: 1536m
    restart: unless-stopped
```

The worker runs as an unprivileged user. It must be able to write the subtitle folders and
`/data/.semsync`, so `user:` should be the owner of your media files (`id -u` / `id -g` of that
account); `./models` must be writable by it too.

The default model (`static`) is downloaded into `/models` on first use (about 220 MB). To run offline, put its
`0_StaticEmbedding/tokenizer.json` and `onnx/model_fp16.onnx` (or the larger
`0_StaticEmbedding/model.safetensors`) in `./models/static`. If you also want `minilm` on a small CPU, make its int8 copy once with
[`tools/quantize_model.py`](../../tools/quantize_model.py) into `./models/minilm`.

| Variable | Default | |
|---|---|---|
| `SEMSYNC_DIR` | `/data/.semsync` | Exchange folder: `queue/`, `failed/`, `state.db`, `semsync.log` |
| `SEMSYNC_MODEL_DIR` | `/models` | Local model copies (`<folder>/static`, `<folder>/minilm`); a model without one is downloaded into `<folder>/hf` |
| `SEMSYNC_MODEL` | `static` | Sentence model: `static` or `minilm` (see [Models](../../docs/models.md)) |
| `SEMSYNC_OUTPUT` | `replace` | `replace`: the correction takes the subtitle's name, the download is kept beside it (see `SEMSYNC_KEEP_DOWNLOAD`). `side`: the download is left as is, the correction is written as `.resync` |
| `SEMSYNC_KEEP_DOWNLOAD` | `visible` | Replace mode: `visible` keeps the download as an extra track, `Movie.fr.untouched.srt`. `hidden` keeps it as `Movie.fr.srt.orig`, which players ignore. Changing it renames the kept files at the next check |
| `SEMSYNC_EXTRA_LINES` | `drop` | Lines the video has no room for (a translator credit, a recap or a scene that your video lacks) are removed. `keep` leaves them where nothing is shown nor said, a block of consecutive lines whole or not at all |
| `SEMSYNC_LOG_MAX_MB` | `10` | Size limit of the log; its oldest entries are deleted beyond it (see [Logs](#logs)). `0`: no limit |
| `SEMSYNC_MEDIA_ROOT` | (none) | Queued jobs whose video or subtitle lies outside this folder (several: separated by `:`) are skipped. Empty: no restriction |

A wrong value (an unknown output mode, keep mode, model or extra-lines setting, a size that is not a
number) stops the worker at start with a message saying which variable, instead of acting as
another value.

The worker runs at the lowest CPU priority (`nice 19`), so a media server transcoding at the
same time keeps priority. It unloads the model when the queue is empty.

### 5. Existing subtitles (optional)

Queue every external `.srt` that sits next to its video. Subtitles already processed and
unchanged are skipped in a fraction of a second, so it can be run again at any time (after an
upgrade, or from a nightly scheduled task):

```bash
docker exec semantic-subsync semantic-subsync-worker backfill /data/media
```

## Logs

`/data/.semsync/semsync.log` is the history of every decision: one JSON line per job, plus one
when the worker starts (version, output mode, settings, model) and one when the queue is empty
again (how many jobs, per status). `unchanged` jobs are only counted there, not logged one by
one, so that re-runs and backfills do not push real history out of the log. The container's
output (`docker logs`) has one line per job, without tracebacks.

Each job line holds:

- the paths, language and kind of the subtitle;
- the decision (`status`, below) and why (`reason`, `reference`, `coverage`);
- the correction: `segments`, `max_abs_offset`, `dropped` lines, and `seg`, the offset applied
  in each segment (from / to in seconds, offset, frame-rate drift in ppm);
- the hashes of the synced subtitle and of the correction (`input_sha256`, `output_sha256`), to
  tell which exact file was processed;
- the engine version and settings, the processing time and the files removed.

| `status` | Meaning |
|---|---|
| `corrected` | Corrected; `seg` lists the offsets applied |
| `in_sync` | Nothing to do |
| `unsure` | Too few lines match the embedded track (wrong reference, other cut): the file is left alone |
| `no_reference` | No embedded text subtitle with at least 20 lines |
| `redundant` | The video embeds a text subtitle of the same language and kind |
| `unchanged` | Already processed, nothing changed since (container output only) |
| `changed_during_run` | Bazarr wrote a new subtitle while this one was processed: nothing is written, the new file is checked as its own job |
| `skipped` | Missing file, not an `.srt`, one of the worker's own files (`.untouched`, `.resync`, `.replaced` of earlier versions), or outside `SEMSYNC_MEDIA_ROOT` |
| `error` | Unexpected failure, with the end of the traceback (log file only); the job is moved to `/data/.semsync/failed` |

Everything logged about one episode or movie, oldest first, in a readable form. A path must
contain every word given, so add a word of the title to an episode number (`S01E02` alone would
list that episode of every series), and the end of the file name to keep one subtitle. Below,
`TITLE` stands for a word of the series name:

```bash
docker exec semantic-subsync semantic-subsync-worker history TITLE S01E02
docker exec semantic-subsync semantic-subsync-worker history TITLE S01E02 .fr.srt   # not .fr.hi.srt
```

Each subtitle starts with its full path, so the series, season and language are explicit.

The log never grows past `SEMSYNC_LOG_MAX_MB` (default 10, several thousand jobs): beyond it, the
oldest entries are deleted and the newest half is kept. No archive copy is made. `0` turns the
limit off.

## State

`/data/.semsync/state.db` (SQLite) keeps one row per downloaded subtitle, which is how the
worker recognises its own corrections and the files it already checked: a file name alone
cannot tell. List it with:

```bash
docker exec semantic-subsync semantic-subsync-worker status            # one line per subtitle + counts
docker exec semantic-subsync semantic-subsync-worker status TITLE S01 # paths containing every word
docker exec semantic-subsync semantic-subsync-worker status --fields  # what each column means
```

Columns: the subtitle and video paths, language and kind; the decision (`status`, `reason`,
`reference`, `coverage`, `segments`, `max_abs_offset`, `dropped`); the hash and size of the
synced download (`input_sha256`, `input_size`); the output (`output_mode`, `output_path`,
`output_sha256`, `replaced_path`); the video's size and date; the engine version and settings;
`origin`, `score`, `secs`, `processed_at` and `runs`.

The worker records what it is about to write before it touches any file, so a container
stopped in the middle of a job is recognised and completed on the next run.

Without `state.db` (deleted, or a new exchange folder), the worker can no longer tell its own
corrections from new downloads. It then checks everything again, takes an existing kept file
(`.untouched`, `.orig`, or `.replaced` of earlier versions) as the download, and keeps anything
it cannot account for as a `.bak` file rather than deleting it. Keep `state.db` with your backups.

## Test one pair by hand

```bash
docker exec semantic-subsync semantic-subsync-worker one "/data/media/Movie/Movie.mkv" "/data/media/Movie/Movie.fr.srt"
```

`one --force` checks it again even if nothing changed.

**Another model for one subtitle.** When a result is unsure or badly corrected, try the other
model on that subtitle:

```bash
docker exec semantic-subsync semantic-subsync-worker one --model minilm "/data/media/Movie/Movie.mkv" "/data/media/Movie/Movie.fr.srt"
```

The choice is kept in `state.db`: a later download of that subtitle is processed with the same
model. `--model default` goes back to `SEMSYNC_MODEL`.
