# How it works

[README](../README.md) · **How it works** · [Results](results.md) · [Usage](usage.md) · [Models](models.md)

What it corrects, one case at a time, on 40 seconds of dialogue. This is a schematic: one mark
per line, blue in sync, orange out of sync, green fixed.

![Offset: every French line is 3 s late and is moved back by 3 s](fix-offset.svg)

![Frame rate: the French drifts more and more and is stretched back](fix-frame-rate.svg)

![Lines missing from the French: the French after the gap is moved later](fix-missing-lines.svg)

![Extra lines in the French: the French after them is moved earlier and the extra lines are left out](fix-extra-lines.svg)

![Wrong reference: no line of the commentary matches the French, the file is left as it is](fix-wrong-reference.svg)

## Steps

1. **Embedding.** Every line is cleaned (tags, hearing-impaired annotations, speaker names) and
   turned into a vector by a multilingual sentence model, run locally on CPU (see
   [Models](models.md)).
2. **Candidates.** Each line, alone or merged with the next one (a sentence split in two), is
   compared with the reference lines, alone or merged by two. The 3 best candidates above a
   similarity threshold are kept.
3. **Anchors.** A weighted longest increasing chain keeps the candidates that respect the order
   of the dialogue. Anchors whose offset disagrees with their neighbours are dropped.
4. **Timeline.** One frame-rate ratio is chosen for the whole file among the standard ones
   (23.976 / 24 / 25 / 29.97 / 30). The timeline is then split into constant-offset segments,
   which absorbs cuts and inserted scenes.
5. **Extra lines.** Some lines have no room in the video:
   - at a cut, a line that would land where the reference says nothing belongs to a scene the
     video does not have;
   - a line that would start before 0 s is typically a "Previously on" recap that the video
     lacks.

   By default they are all removed. With `--extra-lines keep`, they are kept only where
   nothing is shown nor said, consecutive ones as a block, whole or not at all: a translator's
   credit stays, a recap or a whole scene goes. They never overlap another line.

   The result is always in time order.

## Guards

- Fewer than 25 % of lines anchored: **unsure**, the file is left alone: the reference does not
  say the same thing.
- Every correction smaller than 0.5 s: the file is **left untouched**. This is the natural gap
  between two languages, not a sync problem.
- Lines that overlap in the file itself (a watermark, two speakers) are not a reason to rewrite
  it, and a correction keeps them.
- Segments shorter than 60 s are treated as local mismatches, not cuts.

No AI service, no network access once the model is downloaded, and the same input always gives
the same output.
