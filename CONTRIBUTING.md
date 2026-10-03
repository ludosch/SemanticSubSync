# Contributing

Bug reports and pull requests are welcome.

## Reporting a wrong synchronization

Subtitles and videos are copyrighted, so please do not attach them. Instead, include:

- the command and its `--json` output (coverage, segments, offsets);
- what is wrong: for example "lines are 2 s late after 41:00";
- the frame rate of the video and where the subtitle comes from, if known.

## Development

```bash
mise x -- uv run pytest                                               # fast, no model needed (or: uv run pytest)
SEMSYNC_TEST_MODEL=1 mise x -- uv run --extra model pytest -m model   # end to end with each real model
SEMSYNC_CORPUS=~/corpus SEMSYNC_CACHE=~/corpus/emb mise x -- uv run --extra model pytest -m corpus   # local real subtitles
```

### Tests

**Unit tests** run on **synthetic** dialogue (`tests/synth.py`):

- each line carries a "concept" token such as `k17`, which a deterministic fake model turns
  into a fixed vector;
- the two languages of one concept score about 0.9, unrelated lines about 0.1, like the real
  model;
- this tests the algorithm independently of the model, against every distortion of the
  benchmark. No film extract is stored in the repository.

**Corpus tests** (`-m corpus`) run every pair of a local folder of real subtitles (see
[Real-world datasets](docs/results.md#real-world-datasets)). They check the invariants, and that
no correction is clearly worse than the untouched file. They are skipped when `SEMSYNC_CORPUS`
is not set.

**CI** runs on pushes to `main` that change code, not on documentation-only changes.

### Rules

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
