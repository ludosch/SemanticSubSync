# Jellyfin plugin

Re-times the external subtitles of movies and episodes on the text subtitle embedded in the
video, inside Jellyfin: no extra container, no subtitle manager to configure. When too few lines
match the embedded subtitle, the file is left alone.

```
new video, or new / changed .srt ──> plugin ──> semantic-subsync-worker one VIDEO SUB
                                                        │
                       Movie.fr.srt            corrected (keeps its name)
                       Movie.fr.untouched.srt  the download, kept only when a correction was made
                                                        │
                                   item refreshed: Jellyfin shows the tracks at once
```

## Requirements

- Jellyfin **12.1** on 64-bit Linux (x86-64 or ARM64), including the official and linuxserver.io
  Docker images.
- **Write access to the media folders.** Many setups mount the library read-only (`:ro`) in
  the Jellyfin container: remove `:ro` for the plugin to write the corrected subtitle next to the
  video. A read-only folder is reported in the Jellyfin log and skipped.
- About 410 MB of disk in Jellyfin's plugin data folder: the engine (a standalone Python, about
  200 MB) and the model (about 210 MB). Both are downloaded in the background a few minutes after
  Jellyfin starts (the restart that follows the installation); the plugin page shows when the
  engine is ready.

## Install

In Jellyfin: **Dashboard > Plugins > Repositories**, add

```
https://github.com/ludosch/SemanticSubSync/releases/latest/download/manifest.json
```

then install **SemanticSubSync** from the **Catalog** and restart Jellyfin.

Manual install: unzip `jellyfin-plugin-semanticsubsync-<version>.zip` from a
[release](https://github.com/ludosch/SemanticSubSync/releases) into
`<Jellyfin data>/plugins/SemanticSubSync_<version>/` and restart Jellyfin.

## What it does

From the installation on, new subtitles are processed in every library; the ones already there are
left as they are unless you ask for a catch-up.

| When | What |
|---|---|
| **Sync subtitles** in the "..." menu of a movie or an episode (administrators) | All its external `.srt` files, now, in any library; the result shows at the bottom of the screen. Asking again while it runs follows the same sync |
| A movie or an episode is added | Its external `.srt` files, about 30 s after the scan adds it |
| Scheduled task **Re-time new subtitles** (every 30 min, Dashboard > Scheduled Tasks) | The `.srt` files added or changed since the last run, e.g. downloaded by Bazarr or by hand |

- **Subtitles already there.** The first time the plugin sees a library (at its installation, or
  when a library is created, before processing the videos its first scan adds), it records the
  subtitles already in the library's folders and leaves them as they are. Tick
  *Also process the subtitles that were already there (catch-up)* to have the task process them
  too, at any time (on a large library and a small server, this can take hours).
- **Leaving a library out.** Untick it on the plugin page: nothing in it is processed
  automatically; the menu entry still works there.
- **One subtitle at a time**, at the lowest CPU priority. The automatic work **does not start a
  subtitle while someone is watching** (a subtitle already started is finished): it waits for
  playback to end. The menu entry runs at once.
- **Failures.** A subtitle the engine fails on (error, or more than 30 minutes) is tried again by
  the next runs, 3 times at most; then it is left alone until the file changes.
- A subtitle belongs to a video when its name starts with the video's name
  (`Movie.fr.srt`, `Movie.fr.hi.srt` for `Movie.mkv`).
- The decisions are those of the worker: corrected, in sync (nothing changes), unsure (left
  alone), no reference (no embedded text subtitle), redundant (the video already embeds a
  subtitle of the same language and kind). See the [Bazarr integration](../bazarr/README.md#what-the-player-shows)
  for the file names and what the player shows; they are the same.

## Settings

Dashboard > Plugins > SemanticSubSync:

- **New subtitles, automatically, in these libraries**: all of them by default, including
  libraries created later.
- **Also process the subtitles that were already there (catch-up)**: off by default.
- **Correction**: replace the subtitle and keep the download as an extra track titled
  `untouched` (default; Jellyfin plays the correction, as it would have played the download, and
  a newer download from Bazarr takes the name back), or add the correction next to the download as
  `resync`, flagged so that Jellyfin plays it by default.
- **Hide the original download (only the correction is shown)**: off by default. On, the download
  is kept in the same folder as `Movie.fr.srt.orig`, a name Jellyfin and Bazarr do not read, so
  the player lists the correction only; it is still used if the subtitle is checked again, and
  put back if no correction is needed any more. Changing this setting renames the kept files the
  next time the subtitles are checked (the scheduled task does it, without syncing them again).
- **Engine source** (advanced): a wheel path or URL to install instead of the release the plugin
  was built for, e.g. `file:///config/semantic_subsync-0.12.0-py3-none-any.whl`. A
  `semantic-subsync-0.12.0-constraints.txt` next to it pins the dependency versions; without
  one, the latest compatible versions are installed.

The official engine is installed with the dependency versions published with its release
(`semantic-subsync-X.Y.Z-constraints.txt`); the installation fails rather than install other
versions. An installation or an update is built aside and replaces the engine only once its
model is ready and no subtitle is being processed: if it fails, the previous engine keeps
working and the plugin page says why.

## Where things are

Everything lives in `<Jellyfin data>/plugins/Jellyfin.Plugin.SemanticSubSync/`; deleting it
removes the engine, the model and the history.

| Path | Content |
|---|---|
| `engine/` | the semantic-subsync package in a virtual environment, and the dependency versions it was installed with (`constraints.txt`) |
| `python/` | the standalone Python the engine runs on |
| `models/` | the sentence model, at a fixed revision, checked against its SHA-256 |
| `state/semsync.log` | one JSON line per decision |
| `state/state.db` | the worker's record of each subtitle |
| `seen.json` | size and date of each subtitle at its last run (or its failures), to skip unchanged files quickly |
| `libraries.json` | the libraries whose existing subtitles were recorded |

uv, used to install the engine, is deleted once the installation is done. Deleting `seen.json`
is safe: the subtitles of every library are then recorded again as existing ones, not processed.

The worker's `status` and `history` commands work on this folder:

```bash
SEMSYNC_DIR=<plugin folder>/state <plugin folder>/engine/venv/bin/semantic-subsync-worker history TITLE S01E02
```

## The menu entry

The "Sync subtitles" entry is added by a script the plugin inserts into the web client's page as
it is served. It relies on the web client's markup, tested with jellyfin-web 12.1; if a later
version changes it, the entry may be missing (the browser console then says so), and the
automatic processing is not affected. Apps that do not use the web client (TV and mobile apps
with their own interface) do not show it.

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
