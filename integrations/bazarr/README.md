# Bazarr integration

Every subtitle that [Bazarr](https://www.bazarr.media/) downloads is checked in the background
against the text subtitle embedded in the video. When it is out of sync, it is corrected, and
the downloaded version is kept beside it as an extra track to switch back to.

```
Bazarr ──(custom post-processing)──> enqueue.py ──> /data/.semsync/queue/*.job
                                                          │
                              semantic-subsync-worker <───┘
                                          │
                 Movie.fr.srt           corrected (keeps the name Bazarr gave it)
                 Movie.replaced.fr.srt  the download, kept only when a correction was made
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
| `Movie.fr.srt` | French - SUBRIP - External (the correction, listed first) |
| `Movie.replaced.fr.srt` | replaced - French - SUBRIP - External |

If a correction is wrong, pick the "replaced" track. `SEMSYNC_OUTPUT=side` keeps the download
under its own name instead and writes the correction as `Movie.resync.fr.srt` ("resync -
French"). A media server may only see a new file after its next library scan.

When Bazarr downloads a new subtitle over a corrected one (an upgrade, a manual search), the
new file is checked again and the old `.replaced` file is replaced or removed. When a subtitle
needs no correction (any more), it keeps its name and no extra file is left.

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
  matches. Lower it to check only the poorer ones. Note that in our tests the score did not
  predict sync well: subtitles at 69 % were in sync while one at 94 % was off by 44 s.
- **Automatic subtitles synchronization** (Bazarr's own ffsubsync): we recommend turning it
  off, so that the downloaded file stays as published and the worker compares it with the
  original.

### 4. The worker

Build the image from the repository and run it with Docker Compose:

```yaml
services:
  semantic-subsync:
    build: https://github.com/ludosch/SemanticSubSync.git
    container_name: semantic-subsync
    user: "1000:1000"             # same owner as your media files
    environment:
      - SEMSYNC_DIR=/data/.semsync
      - FASTEMBED_CACHE_PATH=/models   # the model is downloaded here on first use
    volumes:
      - /path/to/data:/data          # same mount as in the Bazarr container
      - ./models:/models
    mem_limit: 1536m
    restart: unless-stopped
```

On a small CPU, make the int8 model once with
[`tools/quantize_model.py`](../../tools/quantize_model.py), put it in `./models/minilm-int8`,
and add `SEMSYNC_MODEL_DIR=/models/minilm-int8`.

| Variable | Default | |
|---|---|---|
| `SEMSYNC_OUTPUT` | `replace` | `replace`: the correction takes the subtitle's name, the download is kept as `.replaced`. `side`: the download is left as is, the correction is written as `.resync` |
| `SEMSYNC_EXTRA_LINES` | `drop` | Lines the video has no room for (a translator credit, a recap or a scene that your video lacks) are removed. `keep` leaves them where nothing is shown nor said, a block of consecutive lines whole or not at all |
| `SEMSYNC_LOG_MAX_MB` | `10` | Size limit of the log; its oldest entries are deleted beyond it (see [Logs](#logs)). `0`: no limit |

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
when the worker starts (version, output mode, settings, model).

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
| `unchanged` | Already processed, nothing changed since |
| `skipped` | Missing file, not an `.srt`, or one of our own files (`.replaced`, `.resync`) |
| `error` | Unexpected failure, with the end of the traceback; the job is moved to `/data/.semsync/failed` |

Everything logged about one episode or movie, oldest first, in a readable form. A path must
contain every word given, so add the series name to an episode number (`S04E02` alone would
list that episode of every series), and the end of the file name to keep one subtitle:

```bash
docker exec semantic-subsync semantic-subsync-worker history Ghosts S04E02
docker exec semantic-subsync semantic-subsync-worker history Ghosts S04E02 .fr.srt   # not .fr.hi.srt
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
docker exec semantic-subsync semantic-subsync-worker status Ghosts S04  # paths containing every word
docker exec semantic-subsync semantic-subsync-worker status --fields  # what each column means
```

Columns: the subtitle and video paths, language and kind; the decision (`status`, `reason`,
`reference`, `coverage`, `segments`, `max_abs_offset`, `dropped`); the hash and size of the
synced download (`input_sha256`, `input_size`); the output (`output_mode`, `output_path`,
`output_sha256`, `replaced_path`); the video's size and date; the engine version and settings;
`origin`, `score`, `secs`, `processed_at` and `runs`.

Deleting the file only makes the worker check everything again.

## Test one pair by hand

```bash
docker exec semantic-subsync semantic-subsync-worker one "/data/media/Movie/Movie.mkv" "/data/media/Movie/Movie.fr.srt"
```

`one --force` checks it again even if nothing changed.
