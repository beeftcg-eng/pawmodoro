"""
update_checker.py - "Check and click" updates for Pawmodoro. Pure logic, no Qt.

The app asks GitHub for the latest release of this repo (a short request in a
background thread; see MainWindow._check_for_update). If it's newer than
version.VERSION the header shows an update button, and clicking it walks
through update_dialog.py: download the release's zip, check it, unpack it, and
hand it to the same install script people run by hand (install.ps1 on Windows,
install.sh on Linux), which stops the running app, replaces its files, and --
given the "relaunch" switch added for this -- starts the new version.

Nothing here ever installs anything by itself: every step after the check is
behind a click, and every failure raises UpdateError (never anything else) so
the app just carries on running its current version.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

from version import VERSION

REPO = "beeftcg-eng/pawmodoro"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# The bundle ("Windows (and Linux)": both installers, the app, the ambient sounds) is the one asset
# whose name ends like this; its version is in the middle of the name, so it can't be matched exactly.
ASSET_SUFFIX = "-windows.zip"

CHECK_TIMEOUT = 8
DOWNLOAD_TIMEOUT = 30
CHUNK_BYTES = 1 << 20
WORK_DIR_NAME = "pawmodoro-update"

# Windows process-creation flags (the subprocess constants exist only on Windows, so spelled out).
_CREATE_NEW_CONSOLE = 0x00000010
_CREATE_NEW_PROCESS_GROUP = 0x00000200


class UpdateError(Exception):
    """Anything that goes wrong checking for, fetching or starting an update."""


class UpdateCancelled(UpdateError):
    """The user cancelled the download."""


@dataclass
class Asset:
    name: str
    url: str
    size: int
    sha256: str | None


@dataclass
class Release:
    version: str  # "2.12.0"
    tag: str  # "v2.12.0"
    page: str  # the release's web page
    notes: str  # the release description (markdown)
    asset: Asset | None  # None if the release has no installable bundle attached


# ---------- versions ----------

def parse_version(text):
    """(2, 12, 0) from "v2.12.0" / "2.12.0" / "Pawmodoro v2.12.0"; None if there's no x.y.z in it."""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(candidate, current=VERSION):
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


# ---------- checking ----------

def _request(url, accept):
    return urllib.request.Request(url, headers={"User-Agent": f"Pawmodoro/{VERSION}", "Accept": accept})


def _release_from_json(data):
    asset = None
    for candidate in data.get("assets") or []:
        name = candidate.get("name") or ""
        if name.endswith(ASSET_SUFFIX) and candidate.get("browser_download_url"):
            digest = candidate.get("digest") or ""
            asset = Asset(
                name=name,
                url=candidate["browser_download_url"],
                size=int(candidate.get("size") or 0),
                sha256=digest.split(":", 1)[1].lower() if digest.lower().startswith("sha256:") else None,
            )
            break
    tag = data.get("tag_name") or ""
    parsed = parse_version(tag)
    return Release(
        version=".".join(str(p) for p in parsed) if parsed else tag,
        tag=tag,
        page=data.get("html_url") or RELEASES_PAGE,
        notes=(data.get("body") or "").strip(),
        asset=asset,
    )


def fetch_latest(current=VERSION, opener=urllib.request.urlopen):
    """The newest published release if it's newer than `current`, else None. Raises UpdateError
    if GitHub can't be reached or answers with something unusable."""
    try:
        with opener(_request(LATEST_URL, "application/vnd.github+json"), timeout=CHECK_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise UpdateError(f"GitHub answered {e.code}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise UpdateError(f"couldn't reach GitHub ({getattr(e, 'reason', e)})") from e
    except ValueError as e:
        raise UpdateError("GitHub sent something unreadable") from e

    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    release = _release_from_json(data)
    return release if is_newer(release.tag, current) else None


# ---------- where this copy runs from ----------

def install_dir():
    """Where install.ps1 / install.sh put the app."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Pawmodoro")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "pawmodoro-app")


def is_installed_copy(app_dir=None):
    """True when running from the installer's folder. A copy run from a source checkout
    (`python3 main.py`) must not be "updated" by an installer that writes somewhere else."""
    here = os.path.realpath(app_dir or os.path.dirname(os.path.abspath(__file__)))
    return os.path.normcase(here) == os.path.normcase(os.path.realpath(install_dir()))


def work_dir():
    return os.path.join(tempfile.gettempdir(), WORK_DIR_NAME)


# ---------- fetching ----------

def download_asset(asset, dest_zip, progress=None, should_cancel=None, opener=urllib.request.urlopen):
    """Streams the bundle to `dest_zip`, checking its size and SHA-256 (when GitHub lists one) before
    it's moved into place, so a truncated or tampered download is never unpacked. `progress(loaded,
    total)` is called as it arrives; `should_cancel()` returning True raises UpdateCancelled."""
    os.makedirs(os.path.dirname(dest_zip), exist_ok=True)
    partial = dest_zip + ".part"
    digest = hashlib.sha256()
    loaded = 0
    try:
        with opener(_request(asset.url, "application/octet-stream"), timeout=DOWNLOAD_TIMEOUT) as response:
            total = int(response.headers.get("Content-Length") or asset.size or 0)
            with open(partial, "wb") as out:
                while True:
                    if should_cancel and should_cancel():
                        raise UpdateCancelled("cancelled")
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    loaded += len(chunk)
                    if progress:
                        progress(loaded, total)
        if asset.size and loaded != asset.size:
            raise UpdateError(f"the download was cut short ({loaded} of {asset.size} bytes)")
        if asset.sha256 and digest.hexdigest() != asset.sha256:
            raise UpdateError("the download doesn't match the checksum GitHub lists for it")
        os.replace(partial, dest_zip)
    except UpdateError:
        _remove_quietly(partial)
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        _remove_quietly(partial)
        raise UpdateError(f"the download failed ({getattr(e, 'reason', e)})") from e


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------- unpacking ----------

def extract_zip(zip_path, dest_dir):
    """Unpacks the bundle, refusing any entry that would land outside `dest_dir`."""
    root = os.path.realpath(dest_dir)
    os.makedirs(root, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as bundle:
            for member in bundle.infolist():
                target = os.path.realpath(os.path.join(root, member.filename))
                if os.path.commonpath([root, target]) != root:
                    raise UpdateError("the download contains an unsafe file path")
            bundle.extractall(root)
    except zipfile.BadZipFile as e:
        raise UpdateError("the download isn't a valid zip file") from e


def find_installer_dir(root):
    """The folder inside the unpacked bundle that holds this platform's install script."""
    script = "install.ps1" if sys.platform == "win32" else "install.sh"
    candidates = [root] + [os.path.join(root, name) for name in sorted(os.listdir(root))]
    for folder in candidates:
        if os.path.isfile(os.path.join(folder, script)) and os.path.isfile(os.path.join(folder, "version.py")):
            return folder
    raise UpdateError(f"the download has no {script}")


# ---------- installing ----------

def windows_install_command(script):
    """The PowerShell one-liner that runs install.ps1 and keeps its window open, with the reason, if it fails."""
    quoted = script.replace("'", "''")
    return (
        f"try {{ & '{quoted}' -Relaunch }} "
        "catch { Write-Host $_; Write-Host ''; Read-Host 'The update did not finish. Press Enter to close' }"
    )


def launch_installer(src_dir, platform=None, popen=subprocess.Popen):
    """Starts the platform's install script, detached from this process, so the app can quit while it
    replaces the app's files and then restarts it. Returns the log file's path on Linux, else None."""
    platform = platform or sys.platform
    try:
        if platform == "win32":
            script = os.path.join(src_dir, "install.ps1")
            popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", windows_install_command(script)],
                cwd=src_dir,
                creationflags=_CREATE_NEW_CONSOLE | _CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            return None
        os.makedirs(work_dir(), exist_ok=True)
        log_path = os.path.join(work_dir(), "update.log")
        with open(log_path, "wb") as log:
            popen(
                ["bash", os.path.join(src_dir, "install.sh"), "--relaunch"],
                cwd=src_dir,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
        return log_path
    except OSError as e:
        raise UpdateError(f"couldn't start the installer ({e})") from e


def cleanup_stale_files():
    """Deletes what a previous update left in the temp folder (a ~600 MB bundle and its unpacked
    copy). Best effort: anything still in use is simply left for next time."""
    shutil.rmtree(work_dir(), ignore_errors=True)
