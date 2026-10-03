"""Prepare a release: version, changelog, README download commands, commit and tag. Never pushes.

Usage: uv run python tools/release.py X.Y.Z [--dry-run]
       uv run python tools/release.py --notes X.Y.Z    (CI: prints the changelog notes of X.Y.Z,
                                                        fails when there are none; alias --check-notes)

Checks first: branch main, clean tree, in step with origin/main, last "tests" run green for HEAD,
something under [Unreleased], X.Y.Z above the current version. Then:
  - __version__ in src/semantic_subsync/__init__.py (the only place: pyproject reads it);
  - CHANGELOG.md: the [Unreleased] entries move under "## [X.Y.Z] - <today>", [Unreleased] is
    left empty above it;
  - README.md and README.fr.md: the version in the `pip install ...@vX.Y.Z` command and in the
    `gh release download` / `docker load` example;
  - the Jellyfin plugin: its version (X.Y.Z.0, in Directory.Build.props) and the
    engine release it installs (X.Y.Z);
  - unit tests on the bumped files (on failure every file is restored), then a commit
    "Release vX.Y.Z" and an annotated tag vX.Y.Z.
The push (which starts the release workflow) is printed, to run after a last look.
"""
import datetime, difflib, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "src/semantic_subsync/__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
READMES = [ROOT / "README.md", ROOT / "README.fr.md"]
PLUGIN = ROOT / "integrations/jellyfin"
PLUGIN_FILES = [PLUGIN / "Directory.Build.props",
                PLUGIN / "Jellyfin.Plugin.SemanticSubSync/Engine/EngineInstaller.cs"]
VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.M)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def section(text, title):
    """Body of the '## [title]' section of a Keep a Changelog file, or None."""
    m = re.search(rf"^## \[{re.escape(title)}\][^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else None


def promote(text, version, date):
    """Move the [Unreleased] entries under a new '## [version] - date' section."""
    body = section(text, "Unreleased")
    if body is None or not body.strip():
        raise ValueError("nothing under [Unreleased] in the changelog")
    if section(text, version) is not None:
        raise ValueError(f"the changelog already has a [{version}] section")
    return text.replace(f"## [Unreleased]\n{body}",
                        f"## [Unreleased]\n\n## [{version}] - {date}\n\n{body.strip()}\n\n", 1)


def bump_readme(text, version):
    """The release in the install commands (pip install "...@ git+https://.../SemanticSubSync@vX.Y.Z",
    gh release download vX.Y.Z, semantic-subsync-X.Y.Z-docker-....tar.gz)."""
    text = re.sub(r"(git\+https://github\.com/ludosch/SemanticSubSync@v)\d+\.\d+\.\d+", rf"\g<1>{version}", text)
    text = re.sub(r"(gh release download v)\d+\.\d+\.\d+", rf"\g<1>{version}", text)
    return re.sub(r"(semantic-subsync-)\d+\.\d+\.\d+(-docker-)", rf"\g<1>{version}\g<2>", text)


def bump_plugin(text, version):
    """The Jellyfin plugin's version (X.Y.Z.0: the .props elements; `version:` too, should
    build.yaml carry one again) and the engine release it installs (EngineVersion X.Y.Z)."""
    text = re.sub(r"(<(?:Assembly|File)?Version>)\d+\.\d+\.\d+\.\d+(</)", rf"\g<1>{version}.0\g<2>", text)
    text = re.sub(r'^(version: ")\d+\.\d+\.\d+\.\d+(")', rf"\g<1>{version}.0\g<2>", text, flags=re.M)
    return re.sub(r'(EngineVersion = ")\d+\.\d+\.\d+(")', rf"\g<1>{version}\g<2>", text)


def check_notes(version, text=None):
    body = section(text if text is not None else CHANGELOG.read_text(encoding="utf-8"), version)
    if body is None or not body.strip():
        raise SystemExit(f"CHANGELOG.md has no notes for {version}")
    print(body.strip())


def key(v):
    return tuple(int(x) for x in re.match(r"(\d+)\.(\d+)\.(\d+)", v).groups())


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def preflight(version, current):
    problems = []
    if not SEMVER.match(version):
        problems.append(f"{version} is not X.Y.Z")
    elif key(version) <= key(current) and not (key(version) == key(current) and current != version):
        problems.append(f"{version} is not above the current version {current}")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        problems.append("not on main")
    if git("status", "--porcelain"):
        problems.append("the working tree is not clean")
    git("fetch", "-q", "origin", "main")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        problems.append("HEAD differs from origin/main (push or pull first)")
    problem = ci_problem(git("rev-parse", "HEAD"))
    if problem:
        problems.append(problem)
    return problems


def ci_problem(head, run=subprocess.run):
    """None when the last 'tests' run of commit `head` is green, else what is wrong. tests.yml
    runs on every push to main (documentation-only changes included, with its jobs skipped), so
    every pushed HEAD has a run. `run` is subprocess.run (replaced in the tests)."""
    try:
        p = run(["gh", "run", "list", "--workflow", "tests", "--commit", head, "--json", "status,conclusion", "-L", "1"],
                cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        return "gh (GitHub CLI) not found: install it and run `gh auth login`"
    if p.returncode != 0:
        return f"gh run list failed: {(p.stderr or p.stdout).strip()}"
    last = (json.loads(p.stdout or "[]") or [{}])[0]
    if last.get("conclusion") != "success":
        return f"no green 'tests' run for HEAD ({last.get('status') or 'none'} {last.get('conclusion') or ''})".rstrip()
    return None


def write_and_test(changes, test=None):
    """Write the new contents, then run the unit tests on them. If the tests fail (or anything
    else goes wrong), every file gets its previous content back, so the tree stays clean."""
    old = {path: path.read_bytes() for path in changes}
    try:
        for path, new in changes.items():
            path.write_bytes(new.encode("utf-8"))
        (test or (lambda: subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)))()
    except BaseException:
        for path, content in old.items():
            path.write_bytes(content)
        raise


def main(argv):
    if argv[:1] in (["--notes"], ["--check-notes"]) and len(argv) == 2:
        return check_notes(argv[1])
    dry = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 1:
        raise SystemExit(__doc__)
    version = args[0]
    current = VERSION.search(INIT.read_text(encoding="utf-8")).group(1)
    problems = preflight(version, current)
    if problems and not dry:
        raise SystemExit("Not released:\n- " + "\n- ".join(problems))
    for p in problems:
        print(f"(dry run, would stop here) {p}")

    today = datetime.date.today().isoformat()
    changes = {INIT: VERSION.sub(f'__version__ = "{version}"', INIT.read_text(encoding="utf-8")),
               CHANGELOG: promote(CHANGELOG.read_text(encoding="utf-8"), version, today)}
    changes.update({r: bump_readme(r.read_text(encoding="utf-8"), version) for r in READMES})
    changes.update({p: bump_plugin(p.read_text(encoding="utf-8"), version) for p in PLUGIN_FILES})
    for path, new in changes.items():
        old = path.read_text(encoding="utf-8")
        sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                   str(path.relative_to(ROOT)), str(path.relative_to(ROOT))))
    if dry:
        return
    try:
        write_and_test(changes)
    except subprocess.CalledProcessError:
        raise SystemExit("Not released: the unit tests failed on the bumped files (every file was restored).")
    git("add", *(str(p.relative_to(ROOT)) for p in changes))
    git("commit", "-q", "-m", f"Release v{version}")
    git("tag", "-a", f"v{version}", "-m", f"v{version}")
    print(f"\nCommitted and tagged v{version}. To publish (starts the release workflow):\n"
          f"  git push origin main v{version}")


if __name__ == "__main__":
    main(sys.argv[1:])
