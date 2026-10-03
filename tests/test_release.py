"""tools/release.py: changelog promotion, version bumps and CI check, without git or network."""
import json, subprocess, sys
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


def test_bump_readme_pins_the_pip_install_on_the_new_tag():
    text = ('pip install "semantic-subsync[model] @ git+https://github.com/ludosch/SemanticSubSync@v0.12.0"\n'
            "Since 0.12.0, the default is static.\n")
    out = release.bump_readme(text, "0.13.1")
    assert "SemanticSubSync@v0.13.1\"" in out and "Since 0.12.0," in out


def test_real_readmes_pin_pip_install_on_a_tag():
    for r in release.READMES:
        text = r.read_text(encoding="utf-8")
        assert "git+https://github.com/ludosch/SemanticSubSync@v" in text, r.name
        assert "SemanticSubSync@v99.0.0" in release.bump_readme(text, "99.0.0"), r.name


def test_real_files_have_what_the_script_edits():
    assert release.VERSION.search(release.INIT.read_text(encoding="utf-8"))
    for r in release.READMES:
        text = r.read_text(encoding="utf-8")
        assert release.bump_readme(text, "99.0.0") != text, r.name
    assert release.section(release.CHANGELOG.read_text(encoding="utf-8"), "Unreleased") is not None


def test_bump_plugin_sets_the_plugin_and_engine_versions():
    props = "<Version>0.12.0.0</Version>\n<AssemblyVersion>0.12.0.0</AssemblyVersion>\n<FileVersion>0.12.0.0</FileVersion>\n"
    assert release.bump_plugin(props, "0.13.1").count("0.13.1.0") == 3
    code = 'public const string EngineVersion = "0.12.0";\nprivate const string UvVersion = "0.12.17";\n'
    out = release.bump_plugin(code, "0.13.1")
    assert 'EngineVersion = "0.13.1"' in out and 'UvVersion = "0.12.17"' in out
    build = 'name: "SemanticSubSync"\nversion: "0.12.0.0"\ntargetAbi: "12.1.0.0"\n'
    out = release.bump_plugin(build, "0.13.1")
    assert 'version: "0.13.1.0"' in out and 'targetAbi: "12.1.0.0"' in out


def test_real_plugin_files_have_what_the_script_edits():
    for p in release.PLUGIN_FILES:
        text = p.read_text(encoding="utf-8")
        assert release.bump_plugin(text, "99.0.0") != text, p.name
    # every file of the plugin that carries its version is bumped
    for p in release.PLUGIN.rglob("*"):
        if p.is_file() and p.suffix in (".props", ".yaml", ".csproj", ".cs") and "bin" not in p.parts and "obj" not in p.parts:
            if release.bump_plugin(text := p.read_text(encoding="utf-8"), "99.0.0") != text:
                assert p in release.PLUGIN_FILES, p


def fake_gh(returncode=0, stdout="", missing=False):
    def run(cmd, **kw):
        assert cmd[:3] == ["gh", "run", "list"] and "--commit" in cmd
        if missing:
            raise FileNotFoundError("gh")
        return subprocess.CompletedProcess(cmd, returncode, stdout, "HTTP 401" if returncode else "")
    return run


def test_ci_problem():
    green = json.dumps([{"status": "completed", "conclusion": "success"}])
    assert release.ci_problem("abc", fake_gh(stdout=green)) is None
    red = json.dumps([{"status": "completed", "conclusion": "failure"}])
    assert "no green" in release.ci_problem("abc", fake_gh(stdout=red))
    assert "none" in release.ci_problem("abc", fake_gh(stdout="[]"))
    assert "gh run list failed: HTTP 401" == release.ci_problem("abc", fake_gh(returncode=1))
    assert "not found" in release.ci_problem("abc", fake_gh(missing=True))


def test_write_and_test_restores_every_file_when_the_tests_fail(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"old a\r\n"); b.write_bytes(b"old b\n")

    def failing():
        assert a.read_text() == "new a" and b.read_text() == "new b"
        raise subprocess.CalledProcessError(1, "pytest")
    with pytest.raises(subprocess.CalledProcessError):
        release.write_and_test({a: "new a", b: "new b"}, failing)
    assert a.read_bytes() == b"old a\r\n" and b.read_bytes() == b"old b\n"
    release.write_and_test({a: "new a"}, lambda: None)
    assert a.read_text() == "new a"


def test_jellyfin_manifest():
    import datetime, hashlib
    import jellyfin_manifest as jm
    abi = jm.target_abi(jm.CSPROJ.read_text(encoding="utf-8"))
    assert abi.count(".") == 3
    now = datetime.datetime(2026, 10, 2, 12, 0, tzinfo=datetime.timezone.utc)
    [plugin] = jm.manifest("0.12.0", b"zip", "\n### Added\n- A thing.\n", abi, now)
    assert plugin["guid"] in (jm.ROOT / "integrations/jellyfin/Jellyfin.Plugin.SemanticSubSync/Plugin.cs").read_text(encoding="utf-8")
    assert (jm.ROOT / plugin["imageUrl"].split("/main/", 1)[1]).is_file()   # the logo Jellyfin shows
    [v] = plugin["versions"]
    assert v["version"] == "0.12.0.0" and v["checksum"] == hashlib.md5(b"zip").hexdigest()
    assert v["sourceUrl"].endswith("/releases/download/v0.12.0/jellyfin-plugin-semanticsubsync-0.12.0.zip")
    assert v["changelog"] == "### Added\n- A thing." and v["timestamp"] == "2026-10-02T12:00:00Z"
