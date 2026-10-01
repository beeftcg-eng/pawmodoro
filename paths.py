"""
paths.py - Where Pawmodoro stores its data, per platform:
  Linux:   ~/.local/share/pawmodoro
  Windows: %APPDATA%\\Pawmodoro
  (macOS falls back to the Linux-style path -- untested, but a sane default)
"""
import os
import platform
import shutil


def app_data_dir():
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Pawmodoro")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "pawmodoro")


# Data dirs left behind by features that no longer exist: "webprofile" was
# the embedded YouTube Music browser's profile (QtWebEngine, removed), which
# can weigh tens of MB of cache and cookies.
LEGACY_DIRS = ["webprofile"]


def remove_legacy_dirs():
    """Deletes LEGACY_DIRS from the data dir. Best-effort; never raises."""
    for name in LEGACY_DIRS:
        shutil.rmtree(os.path.join(app_data_dir(), name), ignore_errors=True)
