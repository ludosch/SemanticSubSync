# Jellyfin plugin

Re-times the external subtitles of movies and episodes on the text subtitle embedded in the
video, inside Jellyfin: no extra container, no subtitle manager to configure. When too few lines
match the embedded subtitle, the file is left alone.

```
new video, or new / changed .srt ──> plugin ──> semantic-subsync-worker one VIDEO SUB
                                                        │
                       Movie.fr.srt           corrected (keeps its name)
                       Movie.replaced.fr.srt  the download, kept only when a correction was made
                                                        │
                                   item refreshed: Jellyfin shows the tracks at once
```

## Requirements

- Jellyfin **12.1** on 64-bit Linux (x86-64 or ARM64), including the official and linuxserver.io
  Docker images.
- **Write access to the media folders.** Many setups mount the library read-only (`:ro`) in
  the Jellyfin container: remove `:ro` for the plugin to write the corrected subtitle next to the
  video. A read-only folder is reported in the Jellyfin log and skipped.
- About 700 MB of disk in Jellyfin's plugin data folder: the engine (a standalone Python, about
  250 MB) and the model (about 420 MB), both downloaded on first use.

## Install

In Jellyfin: **Dashboard > Plugins > Repositories**, add

```
https://raw.githubusercontent.com/ludosch/SemanticSubSync/main/integrations/jellyfin/manifest.json
```

then install **SemanticSubSync** from the **Catalog** and restart Jellyfin.

Manual install: unzip `jellyfin-plugin-semanticsubsync-<version>.zip` from a
[release](https://github.com/ludosch/SemanticSubSync/releases) into
`<Jellyfin data>/plugins/SemanticSubSync_<version>/` and restart Jellyfin.

## What it does

Nothing happens on its own until you choose libraries.

| When | What |
|---|---|
| **Sync subtitles** in the "..." menu of a movie or an episode (administrators) | All its external `.srt` files, now, in any library; the result shows at the bottom of the screen |
| A movie or an episode is added to a chosen library | Its external `.srt` files, about 30 s after the scan adds it |
| Scheduled task **Re-time new subtitles** (every 30 min, Dashboard > Scheduled Tasks) | In the chosen libraries, the `.srt` files added or changed since the last run, e.g. downloaded by Bazarr or by hand |

- **Choosing a library.** The subtitles already in it are recorded on the next run of the task and
  left as they are; only the ones added later are processed. Tick *Also process the subtitles
  already in a library when it is chosen* first to process everything (on a large library and a
  small server, this can take hours).
- **One subtitle at a time**, at the lowest CPU priority, and **never while someone is watching**:
  the automatic work waits for playback to end. The menu entry runs at once.
- A subtitle belongs to a video when its name starts with the video's name
  (`Movie.fr.srt`, `Movie.fr.hi.srt` for `Movie.mkv`).
- The decisions are those of the worker: corrected, in sync (nothing changes), unsure (left
  alone), no reference (no embedded text subtitle), redundant (the video already embeds a
  subtitle of the same language and kind). See the [Bazarr integration](../bazarr/README.md#what-the-player-shows)
  for the file names and what the player shows; they are the same.

## Settings

Dashboard > Plugins > SemanticSubSync:

- **Automatic in these libraries**: none by default.
- **Also process the subtitles already in a library when it is chosen.**
- **Correction**: replace the subtitle and keep the download as `replaced` (default), or add the
  correction next to the download as `resync`.
- **Engine source** (advanced): a wheel path or URL to install instead of the release the plugin
  was built for, e.g. `file:///config/semantic_subsync-0.12.0-py3-none-any.whl`.

## Where things are

Everything lives in `<Jellyfin data>/plugins/Jellyfin.Plugin.SemanticSubSync/`; deleting it
removes the engine, the model and the history.

| Path | Content |
|---|---|
| `engine/` | uv, a standalone Python and the semantic-subsync package |
| `models/` | the sentence model (Hugging Face cache) |
| `state/semsync.log` | one JSON line per decision |
| `state/state.db` | the worker's record of each subtitle |
| `seen.json` | size and date of each subtitle at its last run, to skip unchanged files quickly |

The worker's `status` and `history` commands work on this folder:

```bash
SEMSYNC_DIR=<plugin folder>/state <plugin folder>/engine/venv/bin/semantic-subsync-worker history TITLE S01E02
```

## With Bazarr

Use one or the other, not both: two workers with separate records would each take the other's
correction for a download. When switching to the plugin, turn off Bazarr's custom
post-processing and stop the worker container.

## Build

```bash
dotnet publish Jellyfin.Plugin.SemanticSubSync -c Release -o artifacts
```

The plugin is licensed under the GPL-3.0 (it links Jellyfin's libraries); the engine it runs
stays under the MIT license.
