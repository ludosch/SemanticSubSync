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
- A change users can see (engine, command line, worker, image, integrations) comes with its
  entry under `[Unreleased]` in `CHANGELOG.md`, in the same commit.

## Releasing

```bash
uv run python tools/release.py X.Y.Z --dry-run   # shows the changes
uv run python tools/release.py X.Y.Z             # version, changelog, READMEs, tests, commit, tag
git push origin main vX.Y.Z                      # starts the release workflow
```

The script refuses to run outside a clean, pushed `main` whose last CI run is green, or with an
empty `[Unreleased]`.
