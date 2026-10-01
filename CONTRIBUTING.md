# Contributing

Bug reports and pull requests are welcome.

## Reporting a wrong synchronization

Subtitles and videos are copyrighted, so please do not attach them. Instead, include:

- the command and its `--json` output (coverage, segments, offsets);
- what is wrong: for example "lines are 2 s late after 41:00";
- the frame rate of the video and where the subtitle comes from, if known.

## Development

```bash
mise x -- uv run pytest            # or: uv run pytest
```

- Tests must not need the model or any film extract. Add a synthetic case to `tests/synth.py`
  and `tests/test_sync.py` that reproduces the problem; the fake model makes it deterministic.
- For an engine change, write the failing test first, then the fix.
- Everything in the repository is in English: code, comments, documentation, commit messages.
  `README.fr.md` is the only French file and must stay in line with `README.md`.
