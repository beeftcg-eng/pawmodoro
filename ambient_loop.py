"""
ambient_loop.py - Plays a looping ambient sound via whatever command-line
audio player is already on the system (ffplay first choice everywhere,
then paplay/pw-play/aplay on Linux), instead of Qt's bundled multimedia
backend — historically unreliable on a pip-installed PyQt6 on Linux
(fails silently when its backend plugin is missing).

On Windows, if ffplay/ffmpeg isn't installed, falls back to PyQt6's own
QSoundEffect — which is the flipped situation from Linux: Windows' bundled
Qt multimedia backend (Windows Media Foundation) is generally solid, so
this is a reasonable fallback there specifically.

Supports up to a few AmbientLoop instances running concurrently (e.g. Rain
+ Ocean + Wind all at once), each independent, each with its own volume.

Looping strategy: `ffplay` loops a file natively within a single process
(`-loop 0`) — no restart gap at all. Other CLI players are respawned in a
background thread each time they exit (no shell wrapper needed — each is
launched and waited on directly, so stopping it is just proc.terminate()).
Every track here is a long, seamlessly crossfaded loop, so respawns are
rare and the loop point itself is inaudible either way.
"""
import os
import platform
import shutil
import signal
import subprocess
import threading
import time

IS_WINDOWS = platform.system() == "Windows"

RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "resources")

# name -> (display label, filename)
AMBIENT_SOUNDS = {
    "rain": ("Rain", "rain.wav"),
    "ocean": ("Ocean Waves", "ocean.wav"),
    "white_noise": ("White Noise", "white_noise.wav"),
    "wind": ("Wind", "wind.wav"),
    "cafe": ("Caf\u00e9 Ambience", "cafe.wav"),
    "fireplace": ("Fireplace", "fireplace.wav"),
}

MAX_CONCURRENT = 3

# paplay/pw-play/aplay are Linux (PipeWire/PulseAudio/ALSA) specific tools
# with no Windows equivalent; ffplay (ffmpeg) works identically on both.
_SEARCH_ORDER = ("ffplay",) if IS_WINDOWS else ("ffplay", "paplay", "pw-play", "aplay")


def _find_player():
    for name in _SEARCH_ORDER:
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


PLAYER_NAME, PLAYER_PATH = _find_player()
USE_QT_FALLBACK = IS_WINDOWS and PLAYER_NAME is None

print(f"[ambient] audio player: {PLAYER_NAME or 'NONE FOUND (checked ' + ', '.join(_SEARCH_ORDER) + ')'}")
if USE_QT_FALLBACK:
    print("[ambient] falling back to PyQt6's QSoundEffect (Windows, no ffmpeg found)")


class AmbientLoop:
    def __init__(self, sound_key=None, label=None, path=None):
        """Either pass a built-in `sound_key` (looked up in AMBIENT_SOUNDS),
        or an explicit `label` + `path` for a custom user-supplied file."""
        if sound_key is not None:
            if sound_key not in AMBIENT_SOUNDS:
                raise ValueError(f"Unknown ambient sound: {sound_key}")
            self.sound_key = sound_key
            self.label, filename = AMBIENT_SOUNDS[sound_key]
            self.path = os.path.join(RESOURCES_DIR, filename)
        else:
            if not label or not path:
                raise ValueError("Custom AmbientLoop needs both label and path")
            self.sound_key = f"custom:{label}"
            self.label = label
            self.path = path

        self._volume = 50  # 0-100

        # subprocess backend state
        self._thread = None
        self._stop_event = threading.Event()
        self._current_proc = None
        self._proc_lock = threading.Lock()

        # Qt fallback backend state (Windows only, lazily created)
        self._qt_effect = None

    def available(self):
        if USE_QT_FALLBACK:
            return os.path.exists(self.path)
        return PLAYER_PATH is not None and os.path.exists(self.path)

    def likely_compatible(self):
        """ffplay (ffmpeg-based) handles most formats; the Qt fallback and
        the simpler CLI players generally only handle uncompressed WAV."""
        if PLAYER_NAME == "ffplay":
            return True
        return self.path.lower().endswith((".wav", ".wave"))

    def is_playing(self):
        if USE_QT_FALLBACK:
            return self._qt_effect is not None and self._qt_effect.isPlaying()
        return self._thread is not None and self._thread.is_alive()

    def set_volume(self, percent, apply=True):
        """Stores the new level; for the CLI backend, actually applying it
        while already playing means a full restart of the player process
        (these CLI tools have no live-volume control), so callers driving
        this from a slider should pass apply=False on intermediate drag
        events and only apply=True once the value has settled."""
        self._volume = max(0, min(100, percent))
        if USE_QT_FALLBACK:
            if self._qt_effect is not None:
                self._qt_effect.setVolume(self._volume / 100)
        elif apply and self.is_playing():
            self.play()  # CLI players: one-time restart to apply the new level

    def play(self):
        if not self.available():
            print(f"[ambient] {self.sound_key}: play() called but no player/file available")
            return
        self.stop()
        if USE_QT_FALLBACK:
            self._play_qt()
        else:
            print(f"[ambient] {self.sound_key} starting via {PLAYER_NAME}")
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop_worker, daemon=True)
            self._thread.start()

    def stop(self):
        if USE_QT_FALLBACK:
            if self._qt_effect is not None:
                self._qt_effect.stop()
                self._qt_effect = None
            return

        self._stop_event.set()

        # A play() immediately followed by stop() can race: the worker
        # thread may not have registered its process yet. Give it a brief
        # window to do so rather than silently leaving an orphaned process
        # that nothing will ever kill.
        proc = None
        for _ in range(10):
            with self._proc_lock:
                proc = self._current_proc
            if proc is not None or self._thread is None or not self._thread.is_alive():
                break
            time.sleep(0.05)

        if proc is not None:
            # terminate() (SIGTERM on Linux) isn't guaranteed to actually
            # kill the process — if it's still alive shortly after, escalate
            # to kill() (SIGKILL) so "stop" always actually stops the sound.
            # On Linux, signal the whole process group (we launched it with
            # its own session via os.setsid), so any helper child processes
            # a player spawns get cleaned up too, not just the immediate PID.
            try:
                self._signal_proc(proc, terminate=True)
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                try:
                    self._signal_proc(proc, terminate=False)
                    proc.wait(timeout=1.5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            except OSError:
                pass

        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        print(f"[ambient] {self.sound_key} stopped")

    def _signal_proc(self, proc, terminate):
        """terminate=True sends SIGTERM (graceful), False sends SIGKILL
        (forceful). On Linux, signals the whole process group (we launch
        with os.setsid) to catch any helper children too; on Windows,
        Popen.terminate()/kill() are both already forceful equivalents."""
        if IS_WINDOWS:
            proc.terminate() if terminate else proc.kill()
            return
        sig = signal.SIGTERM if terminate else signal.SIGKILL
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            proc.terminate() if terminate else proc.kill()

    # ---------- Qt fallback (Windows without ffmpeg) ----------
    def _play_qt(self):
        from PyQt6.QtMultimedia import QSoundEffect
        from PyQt6.QtCore import QUrl

        self._qt_effect = QSoundEffect()
        self._qt_effect.setSource(QUrl.fromLocalFile(self.path))
        self._qt_effect.setLoopCount(QSoundEffect.Infinite if hasattr(QSoundEffect, "Infinite") else -2)
        self._qt_effect.setVolume(self._volume / 100)
        self._qt_effect.play()

    # ---------- CLI subprocess backend ----------
    def _loop_worker(self):
        while not self._stop_event.is_set():
            cmd = self._build_command_list()
            kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["preexec_fn"] = os.setsid
            try:
                proc = subprocess.Popen(cmd, **kwargs)
            except OSError as e:
                print(f"[ambient] {self.sound_key} failed to start: {e}")
                return

            with self._proc_lock:
                self._current_proc = proc
            proc.wait()
            with self._proc_lock:
                self._current_proc = None

            if PLAYER_NAME == "ffplay":
                # ffplay loops internally (-loop 0); if it exited, we're
                # done (either stopped or it errored) — don't respawn.
                return
            # other players: respawn immediately unless stop was requested

    def _build_command_list(self):
        path = self.path
        if PLAYER_NAME == "ffplay":
            return [PLAYER_PATH, "-nodisp", "-loop", "0", "-volume", str(self._volume), "-loglevel", "quiet", path]
        elif PLAYER_NAME == "paplay":
            pa_vol = int(self._volume / 100 * 65536)
            return [PLAYER_PATH, f"--volume={pa_vol}", path]
        elif PLAYER_NAME == "pw-play":
            vol = round(self._volume / 100, 2)
            return [PLAYER_PATH, f"--volume={vol}", path]
        elif PLAYER_NAME == "aplay":
            return [PLAYER_PATH, "-q", path]
        return [PLAYER_PATH, path]
