# Bazarr integration

Every subtitle that [Bazarr](https://www.bazarr.media/) downloads is checked in the background
against the text subtitle embedded in the video. When it is out of sync, a corrected copy is
written next to it. The downloaded file itself is never modified, so Bazarr keeps managing it
(upgrades, deletion) as usual.

```
Bazarr ──(custom post-processing)──> enqueue.py ──> /data/.semsync/queue/*.job
                                                          │
                              semantic-subsync-worker <───┘
                                          │
                 Movie.fr.srt  (downloaded, untouched)
                 Movie.semsync.fr.srt  (written only when a correction is needed)
```

Bazarr has no plugin system for synchronization engines: its built-in sync (ffsubsync) is
hard-wired. Its **custom post-processing** command is the supported extension point, so this
integration uses it. The command only drops a small job file; the work runs in a separate
container, so Bazarr is never slowed down and does not need the model.

## Why a separate file?

Media servers such as Jellyfin show `Movie.semsync.fr.srt` as an extra French subtitle titled
"semsync", next to the original. You can compare both, and nothing is lost if a correction is
wrong. A stale `.semsync` file is removed when a newer download turns out to be in sync or
cannot be checked.

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

`SEMSYNC_EXTRA_LINES=drop` removes every line the video has no room for (a translator credit,
a recap or a scene that your video lacks). By default (`keep`) such lines stay where nothing is
shown nor said, a block of consecutive lines whole or not at all.

The worker runs at the lowest CPU priority (`nice 19`), so a media server transcoding at the
same time keeps priority. It unloads the model when the queue is empty.

### 5. Existing subtitles (optional)

Queue every external `.srt` that sits next to its video:

```bash
docker exec semantic-subsync semantic-subsync-worker backfill /data/media
```

## Logs

One JSON line per job in `/data/.semsync/semsync.log`:

| `status` | Meaning |
|---|---|
| `corrected` | A `.semsync` file was written; `seg` lists the offsets applied |
| `in_sync` | Nothing to do |
| `refused` | The embedded track does not match this subtitle (coverage too low) |
| `no_reference` | No embedded text subtitle with at least 20 lines |
| `skipped` | Missing file, not an `.srt`, or one of our own outputs |
| `error` | Unexpected failure; the job is moved to `/data/.semsync/failed` |

## Test one pair by hand

```bash
docker exec semantic-subsync semantic-subsync-worker one "/data/media/Movie/Movie.mkv" "/data/media/Movie/Movie.fr.srt"
```
