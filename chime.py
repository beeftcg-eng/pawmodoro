"""
chime.py - A short, soft two-note chime for when a pomodoro phase ends
(the tray notification alone is easy to miss if you've looked away).

The sound is synthesized with the standard library into a small WAV file in
the app's data folder the first time it's needed, so there's nothing to
download and no extra dependency. It's played through whatever the ambient
sounds already use (ffplay / paplay / pw-play / aplay on Linux), or the
built-in winsound module on Windows.
"""
import math
import os
import platform
import struct
import subprocess
import threading
import wave

from paths import app_data_dir

IS_WINDOWS = platform.system() == "Windows"
SAMPLE_RATE = 44100
CHIME_PATH = os.path.join(app_data_dir(), "chime.wav")


def _bell(freq, seconds, start, samples, gain):
    """Adds a decaying sine "bell" (fundamental + a quiet 2x overtone)."""
    first = int(start * SAMPLE_RATE)
    for i in range(int(seconds * SAMPLE_RATE)):
        t = i / SAMPLE_RATE
        envelope = math.exp(-3.2 * t) * min(1.0, t / 0.005)  # 5ms attack avoids a click
        value = math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(2 * math.pi * freq * 2 * t)
        if first + i < len(samples):
            samples[first + i] += gain * envelope * value


def ensure_chime_file():
    """Writes the chime WAV if it isn't there yet; returns its path (or None
    if the data folder isn't writable)."""
    if os.path.exists(CHIME_PATH):
        return CHIME_PATH
    try:
        total = 1.9
        samples = [0.0] * int(total * SAMPLE_RATE)
        _bell(880.0, 1.4, 0.0, samples, 0.45)      # A5
        _bell(1318.5, 1.5, 0.28, samples, 0.40)    # E6, a fifth up
        os.makedirs(os.path.dirname(CHIME_PATH), exist_ok=True)
        with wave.open(CHIME_PATH, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(
                struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples
            ))
        return CHIME_PATH
    except OSError:
        return None


def play_chime():
    """Plays the chime without blocking. Silent (never raises) if no player
    is available."""
    path = ensure_chime_file()
    if path is None:
        return
    if IS_WINDOWS:
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except (ImportError, RuntimeError):
            pass
        return

    from ambient_loop import PLAYER_NAME, PLAYER_PATH
    if PLAYER_PATH is None:
        return
    if PLAYER_NAME == "ffplay":
        cmd = [PLAYER_PATH, "-nodisp", "-autoexit", "-volume", "70", "-loglevel", "quiet", path]
    elif PLAYER_NAME == "aplay":
        cmd = [PLAYER_PATH, "-q", path]
    else:  # paplay / pw-play
        cmd = [PLAYER_PATH, path]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return
    # reap the finished process so it doesn't linger as a zombie
    threading.Thread(target=proc.wait, daemon=True).start()
