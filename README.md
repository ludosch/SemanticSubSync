**English** | [Français](README.fr.md)

# SemanticSubSync

Re-times a downloaded subtitle in the cases where the usual sync tools fail: a scene added or
cut, a different frame rate, or both. It compares **what is said**, line by line, with a
subtitle already in sync (usually the one embedded in the video), even in another language. When
that reference does not match, it says so and leaves the file alone instead of reporting a
success.

```bash
semantic-subsync Movie.fr.srt Movie.mkv     # reference: the subtitle embedded in the video
```

In Jellyfin, a [plugin](integrations/jellyfin/README.md) does it automatically for every new
subtitle, and from the menu of a movie or an episode.

## Why

Subtitles downloaded for a movie or an episode are often made for another release of the same
video: another frame rate (23.976 vs 25 fps), a scene added or cut, a different intro. The
result drifts, or is fine for 20 minutes and then off by 4 seconds.

Yet most videos already carry a perfectly timed subtitle: the embedded track, often in the
original language. SemanticSubSync uses it as a reference and matches lines **by meaning**, with
a small multilingual sentence model: "Where did you put the keys?" and "Où as-tu mis les clés ?"
are recognized as the same line.

## Where other tools fail

One example, built for this project ([`examples/lighthouse`](examples/lighthouse)). Each tool
gets the same subtitle files, with a reference subtitle that is in sync with the video.

| Tool | Scene missing + 25 fps | Extra scene | Wrong reference (commentary track) |
|---|---|---|---|
| alass 2.0.0 | ✅ in sync | ❌ part of the file off | ❌ rewrites the file instead of leaving it alone |
| ffsubsync 0.5.1 (with or without `--split-penalty`) | ❌ half of the file off | ❌ half of the file off | ❌ rewrites the file instead of leaving it alone |
| LAPSE 2.2.3 | ❌ half of the file off, says "solid" | ❌ half of the file off, says "solid" | ❌ rewrites the file and says "solid" |
| **SemanticSubSync** | ✅ **in sync** | ✅ **in sync** | ✅ **unsure, file left alone** |

This describes these files only, not every video. The figures, the details and the scripts to
rerun it are in the example folder.

Two failures come back in this example and in the [benchmark](docs/results.md):

- **Cuts and inserted scenes.** Aligning on the audio or on the timing pattern of another
  subtitle handles an offset or a frame-rate change; after a scene missing or added, part of the
  file often stays off. SemanticSubSync knows which line is which, so it finds where the cut is
  and moves each part by its own offset.
- **Failures reported as success.** A file left off by a minute, or re-timed on a commentary
  track, comes out as a success. SemanticSubSync counts the lines it could match: below 25 %, it
  is **unsure** and writes nothing.

## Example

A French subtitle was downloaded for a 10-minute episode (an
[original dialogue](examples/lighthouse) written for this project).

- It was timed on a TV broadcast: 25 fps instead of the video's 23.976, and one 70-second scene
  missing.
- The video carries an English subtitle, in sync.

```console
$ semantic-subsync downloaded.fr.srt reference.en.srt
corrected -> downloaded.fr.synced.srt (2 segment(s), largest shift +90.68 s) [reference: reference.en.srt]
```

| Line | Heard in the video at | Before | After |
|---|---|---|---|
| Il y a quelqu'un là-haut ? | 0:20.0 | 0:19.2 (0.8 s early) | 0:20.0 ✅ |
| Au crochet près de la porte. Pourquoi ? | 4:18.9 | 4:08.3 (10.6 s early) | 4:18.9 ✅ |
| *the missing scene* | 4:45 to 5:55 | | |
| Encore. Plus fort cette fois. | 6:18.7 | 4:55.9 (82.8 s early) | 6:18.7 ✅ |
| Oui ? | 9:26.5 | 7:56.0 (90.5 s early) | 9:26.4 ✅ |

## How it works

One mark per line, blue in sync, orange out of sync, green fixed:

![Lines missing from the French: the French after the gap is moved later](docs/fix-missing-lines.svg)

![Wrong reference: no line of the commentary matches the French, the file is left as it is](docs/fix-wrong-reference.svg)

- Each line is matched with the reference lines that say the same thing, in the order of the
  dialogue.
- One frame rate is chosen for the whole file, then the timeline is split into constant-offset
  segments: this absorbs cuts and inserted scenes.
- Lines the video has no room for (a scene it lacks, a "Previously on" recap) are removed.
- Fewer than 25 % of lines matched: **unsure**, the file is left alone. Every correction smaller
  than 0.5 s: the file is **left untouched**.

No AI service, no network access once the model is downloaded, and the same input always gives
the same output. The other cases (offset, frame rate, extra lines), each step and each guard:
[How it works](docs/how-it-works.md).

## Results

These figures describe the files they were measured on, not what the tool will do on any video.
They were measured with the minilm model (the benchmark on version 0.7); the default static
model, run again on the same files, passed as many benchmark cases and made the same decisions
on the datasets.

- **Benchmark** (105 distorted embedded tracks: offsets, frame-rate changes, cuts, inserted and
  removed scenes): SemanticSubSync passed 104 cases; alass passed 84 and had 10 gross failures,
  none of them reported.
- **Real-world datasets** (114 pairs of downloaded subtitles in 16 languages): 66 left untouched,
  already in sync; 44 corrected, with the median share of lines within 1 s of their translation
  going from 4.5 % to 91.5 %; 4 unsure, whose files belonged to another episode.

Method, speed on a NAS and every figure: [Results](docs/results.md).

## Scope

### Good fit

- The video has an embedded **text** subtitle (SRT, ASS, WebVTT, mov_text), in any language.
- Or you have another subtitle file that you know is in sync with your video.
- The subtitle to fix is a regular `.srt`.

### Not a fit

- No reference at all: this tool does not listen to the audio. Use ffsubsync or alass.
- Bitmap-only embedded subtitles (PGS, VobSub): they would need OCR, which is out of scope.
- A reference that comes from another release than your video (e.g. a downloaded English
  subtitle): it has the same timing problems as the file you want to fix.

## Get started

### Jellyfin

A plugin that installs the engine itself, no extra container. Under **Dashboard > Plugins >
Repositories**, add

```
https://github.com/ludosch/SemanticSubSync/releases/latest/download/manifest.json
```

then install **SemanticSubSync** from the catalog and restart Jellyfin. Jellyfin needs write
access to the media folders: see the [plugin guide](integrations/jellyfin/README.md).

### Bazarr

Each downloaded subtitle is checked by a background worker and corrected when needed; the
download stays available as an extra track. See the [Bazarr guide](integrations/bazarr/README.md).

### Command line

Python 3.11 or later, on 64-bit Linux (x86-64 or ARM64), macOS or Windows; ffmpeg when the
reference is a video.

```bash
pip install "semantic-subsync[model] @ git+https://github.com/ludosch/SemanticSubSync@v0.12.0"
```

The default model (about 220 MB) is downloaded from Hugging Face on first use.

Each [release](https://github.com/ludosch/SemanticSubSync/releases) also carries the Python
package and a Docker image for amd64 and arm64, to load with `docker load`:

```bash
gh release download v0.12.0 -R ludosch/SemanticSubSync -p "*docker-amd64*"
docker load -i semantic-subsync-0.12.0-docker-amd64.tar.gz
```

```bash
semantic-subsync SUBTITLE REFERENCE [-o OUTPUT]
```

- `REFERENCE` is a `.srt` in sync with the video, or the video itself (its fullest embedded text
  subtitle is used).
- The input is never modified: the result goes to `SUBTITLE.synced.srt`; nothing is written when
  it is already in sync.
- Exit status: `0` corrected or already in sync, `1` unsure (nothing written), `2` error.

Every option, encodings and the Python API: [Usage](docs/usage.md). The two sentence models and
the languages they cover: [Models](docs/models.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) (tests, release) and the [changelog](CHANGELOG.md).

## Acknowledgements

- [ffsubsync](https://github.com/smacke/ffsubsync) and [alass](https://github.com/kaegi/alass),
  the reference tools this project was measured against.
- [DuoSubs](https://github.com/CK-Explorer/DuoSubs), whose sentence-level approach inspired the
  matching of split sentences.

## License

[MIT](LICENSE)
