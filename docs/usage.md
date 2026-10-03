# Usage

[README](../README.md) · [How it works](how-it-works.md) · [Results](results.md) · **Usage** · [Models](models.md)

## Installation

- Python 3.11 or later, on 64-bit Linux, macOS or Windows.
- ffmpeg / ffprobe, only when the reference is a video.
- ARM64 (Raspberry Pi class hardware) is covered by the CI: tests, both models, the example
  and the Docker image run on an ARM64 machine, with the same results as on x86-64.
- A Raspberry Pi needs a 64-bit OS: the ONNX runtime has no 32-bit ARM build.

The install commands and the Docker image are in the [README](../README.md#command-line).

## Command line

```bash
semantic-subsync SUBTITLE REFERENCE [-o OUTPUT] [--track INDEX] [--lang CODE]
                 [--extra-lines drop|keep] [--min-coverage X] [--model NAME] [--json]
```

**Reference.** `REFERENCE` is a `.srt` in sync with the video, or the video itself. With a
video, the fullest embedded text subtitle is used, in any language, forced tracks excluded.
`--track` picks a stream by its ffprobe index instead.

**Output.**

- The input subtitle is never modified. The result goes to `SUBTITLE.synced.srt` by default
  (`-o` to change it).
- Already in sync: nothing is written.
- Exit status: `0` corrected or already in sync, `1` unsure (nothing written), `2` error.
- `--json` prints the decision and the statistics (coverage, segments, offsets, lines dropped,
  extra lines kept).

**Extra lines.** The lines the video has no room for are removed (see
[How it works](how-it-works.md#steps)). `--extra-lines keep` leaves them where nothing is shown
nor said.

**Coverage.** Below 25 % of lines anchored, the result is unsure and nothing is written;
`--min-coverage` changes that threshold (see [Results](results.md#benchmark-on-embedded-tracks)).

**Model.** `--model` picks the sentence model (see [Models](models.md)).

### Encodings

Files that are not UTF-8 (most older downloads) are read in the code page of their language:

- taken from the file name (`Movie.ru.srt`, `Show.S01E01.pt-BR.srt`) or from `--lang ru`;
- without either, a Western code page is assumed, unless the text then looks like another
  script, in which case the encoding is guessed.

## From Python

```python
from semantic_subsync import core, media

status, cues, stats = core.resync(media.read_srt("Movie.fr.srt"), core.parse("Movie.en.srt"))
if status == "corrected":
    core.write("Movie.fr.synced.srt", cues)
```

## Integrations

The engine knows nothing about media servers or subtitle managers. Integrations live in
[`integrations/`](../integrations):

- [**Bazarr**](../integrations/bazarr/README.md): each downloaded subtitle is checked
  automatically by a background worker and corrected when needed. The downloaded version stays
  available as an extra track, and every decision is logged.
- [**Jellyfin**](../integrations/jellyfin/README.md): a plugin that installs the engine itself.
  "Sync subtitles" in the menu of a movie or an episode re-times its subtitles; from the
  installation on, new videos and new subtitle files are handled automatically (the ones already
  there on request). The item is refreshed so the tracks show at once. No extra container;
  Jellyfin needs write access to the media folders.
