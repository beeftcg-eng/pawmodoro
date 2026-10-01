#!/usr/bin/env python3
"""
build_bundle.py - Builds the release zip the in-app updater downloads
(update_checker.py): pawmodoro-v<VERSION>-windows.zip, holding one
"pawmodoro/" folder with every tracked file at HEAD plus the generated
ambient sounds (resources/*.wav, gitignored -- run tools/generate_rain.py and
tools/generate_ambient.py first).

    python tools/build_bundle.py [--out DIR]

Prints the zip's path. Refuses to build if anything the updater or the app
needs is missing, so a broken bundle can't be released by accident.
Used by .github/workflows/release.yml; works the same locally.
"""
import argparse
import glob
import io
import os
import subprocess
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ambient_loop import AMBIENT_SOUNDS  # noqa: E402
from version import VERSION  # noqa: E402

FOLDER = "pawmodoro"
# What update_checker.py / the installers rely on being inside the folder.
REQUIRED = ["install.sh", "install.ps1", "install.bat", "version.py", "main.py",
            "requirements.txt", "windows_launcher/Pawmodoro.exe"]


def tracked_files():
    """{path: bytes} for every file tracked at HEAD (what `git archive` ships)."""
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT,
                             check=True, capture_output=True).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                files[member.name] = (tar.extractfile(member).read(), member.mode)
    return files


def build(out_dir):
    files = tracked_files()
    sounds = {os.path.basename(path) for path in glob.glob(os.path.join(ROOT, "resources", "*.wav"))}
    needed_sounds = {filename for _label, filename in AMBIENT_SOUNDS.values()}
    missing = [f for f in REQUIRED if f not in files] + sorted(needed_sounds - sounds)
    if missing:
        raise SystemExit("Can't build the bundle, missing: " + ", ".join(missing)
                         + "\n(sounds: run tools/generate_rain.py and tools/generate_ambient.py)")

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"pawmodoro-v{VERSION}-windows.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, (data, mode) in sorted(files.items()):
            info = zipfile.ZipInfo(f"{FOLDER}/{name}", date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = (mode & 0o777 | 0o100000) << 16  # keep install.sh executable
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
        for name in sorted(needed_sounds):
            zf.write(os.path.join(ROOT, "resources", name), f"{FOLDER}/resources/{name}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=os.path.join(ROOT, "dist"))
    print(build(parser.parse_args().out))


if __name__ == "__main__":
    main()
