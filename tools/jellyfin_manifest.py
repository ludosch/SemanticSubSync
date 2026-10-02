"""Jellyfin plugin repository manifest for one release, printed as JSON (release workflow).

Usage: python tools/jellyfin_manifest.py X.Y.Z PLUGIN_ZIP NOTES_FILE > manifest.json

Jellyfin reads it from the URL added under Dashboard > Plugins > Repositories (the latest
release's manifest.json), downloads the zip from the release of the same tag and checks its MD5.
The Jellyfin version it needs (targetAbi) is the one of the Jellyfin.Controller package the
plugin is built against.
"""
import datetime, hashlib, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSPROJ = ROOT / "integrations/jellyfin/Jellyfin.Plugin.SemanticSubSync/Jellyfin.Plugin.SemanticSubSync.csproj"
REPO = "https://github.com/ludosch/SemanticSubSync"

PLUGIN = {
    "guid": "32462154-51a5-4097-ac51-e453bb9dd38d",
    "name": "SemanticSubSync",
    "overview": "Re-times downloaded subtitles on the subtitle embedded in the video, by meaning.",
    "description": (
        "Re-times the external subtitles of movies and episodes on the text subtitle embedded in "
        "the video, by matching lines on meaning with a small multilingual sentence model. Handles "
        "offsets, frame-rate changes, cuts and inserted scenes; leaves the file alone when unsure. "
        "\"Sync subtitles\" in the menu of a movie or an episode, and automatic in the libraries "
        "chosen on the plugin page. The engine is installed in the plugin's data folder on first "
        "use. Needs write access to the media folders."),
    "owner": "ludosch",
    "category": "Metadata",
}


def target_abi(csproj_text):
    """'12.1.0' from <PackageReference Include="Jellyfin.Controller" Version="12.1.0"> -> '12.1.0.0'."""
    m = re.search(r'Include="Jellyfin\.Controller" Version="(\d+\.\d+\.\d+)"', csproj_text)
    if not m:
        raise ValueError("no Jellyfin.Controller version in the project file")
    return m.group(1) + ".0"


def manifest(version, zip_bytes, notes, abi, now):
    entry = {
        "version": f"{version}.0",
        "changelog": notes.strip(),
        "targetAbi": abi,
        "sourceUrl": f"{REPO}/releases/download/v{version}/jellyfin-plugin-semanticsubsync-{version}.zip",
        "checksum": hashlib.md5(zip_bytes).hexdigest(),
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return [dict(PLUGIN, versions=[entry])]


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    version, zip_path, notes_path = argv
    out = manifest(version, Path(zip_path).read_bytes(), Path(notes_path).read_text(encoding="utf-8"),
                   target_abi(CSPROJ.read_text(encoding="utf-8")), datetime.datetime.now(datetime.timezone.utc))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
