"""
paths.py - Where Pawmodoro stores its data, per platform:
  Linux:   ~/.local/share/pawmodoro
  Windows: %APPDATA%\\Pawmodoro
  (macOS falls back to the Linux-style path -- untested, but a sane default)
"""
import os
import platform


def app_data_dir():
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Pawmodoro")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "pawmodoro")
