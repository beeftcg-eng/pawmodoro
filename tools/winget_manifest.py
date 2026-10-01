#!/usr/bin/env python3
"""
winget_manifest.py - Writes the winget manifests (version, installer,
defaultLocale) for a published release, ready to submit as a pull request
to github.com/microsoft/winget-pkgs under manifests/b/beeftcg/Pawmodoro/<version>/.

    python tools/winget_manifest.py --license "MIT" [--version 2.17.0] [--out DIR]

Reads the release's Pawmodoro-Setup.exe URL and sha256 from the GitHub API
(the asset's `digest`), so it only works once the release is published.
The installer is Inno Setup, per-user (no admin), and needs Python, which
winget installs first through the dependency below.
"""
import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from version import VERSION  # noqa: E402

REPO = "beeftcg-eng/pawmodoro"
PACKAGE_ID = "beeftcg.Pawmodoro"
MANIFEST_VERSION = "1.12.0"
SCHEMA = "https://aka.ms/winget-manifest.{kind}.1.12.0.schema.json"
ASSET = "Pawmodoro-Setup.exe"
PYTHON_PACKAGE = "Python.Python.3.12"


def release_asset(version):
    url = f"https://api.github.com/repos/{REPO}/releases/tags/v{version}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"}), timeout=20) as r:
        release = json.load(r)
    asset = next((a for a in release.get("assets", []) if a["name"] == ASSET), None)
    if asset is None:
        raise SystemExit(f"v{version} has no {ASSET} attached (yet)")
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise SystemExit(f"GitHub gave no sha256 for {ASSET}")
    return asset["browser_download_url"], digest.split(":", 1)[1].upper(), release.get("published_at", "")[:10]


def manifests(version, license_name, installer_url, sha256, release_date):
    header = "# Created with tools/winget_manifest.py in the Pawmodoro repo\n"
    version_yaml = f"""{header}# yaml-language-server: $schema={SCHEMA.format(kind="version")}
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: {MANIFEST_VERSION}
"""
    installer_yaml = f"""{header}# yaml-language-server: $schema={SCHEMA.format(kind="installer")}
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
InstallerType: inno
Scope: user
InstallModes:
  - interactive
  - silent
  - silentWithProgress
UpgradeBehavior: install
Dependencies:
  PackageDependencies:
    - PackageIdentifier: {PYTHON_PACKAGE}
ReleaseDate: {release_date}
Installers:
  - Architecture: x64
    InstallerUrl: {installer_url}
    InstallerSha256: {sha256}
ManifestType: installer
ManifestVersion: {MANIFEST_VERSION}
"""
    locale_yaml = f"""{header}# yaml-language-server: $schema={SCHEMA.format(kind="defaultLocale")}
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
PackageLocale: en-US
Publisher: beeftcg
PublisherUrl: https://github.com/beeftcg-eng
PackageName: Pawmodoro
PackageUrl: https://github.com/{REPO}
LicenseUrl: https://github.com/{REPO}/blob/main/LICENSE
License: {license_name}
ShortDescription: Notes, a recurring checklist, a pomodoro timer and ambient sounds, with XP and quests.
Description: |-
  A pen-and-paper styled desktop app combining rich-text notes, a recurring checklist with reminders,
  a pomodoro timer, ambient sounds, music controls and gamification (XP, levels, daily and weekly quests).
  Optional cloud sync keeps it in step with a phone web app.
Tags:
  - pomodoro
  - productivity
  - notes
  - checklist
  - timer
ReleaseNotesUrl: https://github.com/{REPO}/releases/tag/v{version}
ManifestType: defaultLocale
ManifestVersion: {MANIFEST_VERSION}
"""
    return {
        f"{PACKAGE_ID}.yaml": version_yaml,
        f"{PACKAGE_ID}.installer.yaml": installer_yaml,
        f"{PACKAGE_ID}.locale.en-US.yaml": locale_yaml,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--license", required=True, help='e.g. "MIT", or "Proprietary"')
    parser.add_argument("--out", default=os.path.join(ROOT, "dist", "winget"))
    args = parser.parse_args()
    url, sha256, released = release_asset(args.version)
    folder = os.path.join(args.out, "manifests", "b", "beeftcg", "Pawmodoro", args.version)
    os.makedirs(folder, exist_ok=True)
    for name, text in manifests(args.version, args.license, url, sha256, released).items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(text)
    print(folder)


if __name__ == "__main__":
    main()
