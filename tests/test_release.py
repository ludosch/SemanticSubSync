"""tools/release.py: changelog promotion and README version, without git or network."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import release  # noqa: E402

LOG = """# Changelog

Intro.

## [Unreleased]

### Added
- A thing.

### Fixed
- A bug.

## [0.10.0] - 2026-10-02

### Added
- Older.
"""


def test_promote_moves_unreleased_under_the_new_version():
    out = release.promote(LOG, "0.11.0", "2026-10-03")
    assert release.section(out, "Unreleased").strip() == ""
    assert release.section(out, "0.11.0").strip() == "### Added\n- A thing.\n\n### Fixed\n- A bug."
    assert "## [0.11.0] - 2026-10-03\n" in out
    assert out.index("## [Unreleased]") < out.index("## [0.11.0]") < out.index("## [0.10.0]")
    assert release.section(out, "0.10.0") == release.section(LOG, "0.10.0")


def test_promote_refuses_an_empty_unreleased_and_a_known_version():
    with pytest.raises(ValueError, match="nothing under"):
        release.promote(release.promote(LOG, "0.11.0", "2026-10-03"), "0.12.0", "2026-10-04")
    with pytest.raises(ValueError, match="already has"):
        release.promote(LOG, "0.10.0", "2026-10-03")


def test_check_notes():
    release.check_notes("0.10.0", LOG)
    with pytest.raises(SystemExit):
        release.check_notes("0.9.0", LOG)
    with pytest.raises(SystemExit):
        release.check_notes("0.11.0", LOG.replace("- Older.\n", ""))  # absent
    with pytest.raises(SystemExit):
        release.check_notes("0.10.0", LOG.replace("### Added\n- Older.\n", ""))   # empty


def test_bump_readme_changes_the_download_example_only():
    text = ('gh release download v0.10.0 -R ludosch/SemanticSubSync -p "*docker-amd64*"\n'
            "docker load -i semantic-subsync-0.10.0-docker-amd64.tar.gz\n"
            "Engine 0.10.0 changed the default.\n")
    out = release.bump_readme(text, "0.11.0")
    assert "gh release download v0.11.0 " in out and "semantic-subsync-0.11.0-docker-amd64" in out
    assert "Engine 0.10.0 changed" in out


def test_real_files_have_what_the_script_edits():
    assert release.VERSION.search(release.INIT.read_text(encoding="utf-8"))
    for r in release.READMES:
        text = r.read_text(encoding="utf-8")
        assert release.bump_readme(text, "99.0.0") != text, r.name
    assert release.section(release.CHANGELOG.read_text(encoding="utf-8"), "Unreleased") is not None
