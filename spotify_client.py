"""
spotify_client.py - Optional Spotify Connect integration.

Uses OAuth Authorization Code + PKCE (no client secret needed, safe for a
desktop app) via Python's stdlib only (http.server, urllib) — no new pip
dependency. You'll need your own free Spotify Developer app (see README
"Connecting Spotify" for the two-minute setup) since Pawmodoro can't ship
a redirect URI pre-registered to someone else's app.

Important: this controls Spotify Connect — it does NOT stream audio
itself. It sends commands to whichever device already has Spotify open
and active (desktop app, phone, web player). If nothing is currently
active, calls return a clear "no active device" message rather than
failing silently.

Note on testing: I don't have network access or a Spotify account in the
environment I built this in, so this follows Spotify's documented API
contract carefully but hasn't been exercised against their live servers.
If you hit an auth or API error, paste it back and I'll fix it fast.
"""
import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REDIRECT_PORT = 8890
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


def _generate_pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]
        self.server.auth_result = {"code": code, "error": error}

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = "Connected! You can close this tab and go back to Pawmodoro." if code else f"Spotify sign-in failed: {error}"
        self.wfile.write(
            f"<html><body style='font-family:sans-serif;padding:40px;'><h2>{msg}</h2></body></html>".encode()
        )

    def log_message(self, format, *args):
        pass  # silence default per-request stderr logging


class SpotifyClient:
    def __init__(self, storage):
        self.storage = storage

    # ---------- Connection state ----------
    def is_connected(self):
        return bool(self.storage.get_spotify_config().get("refresh_token"))

    def client_id(self):
        return self.storage.get_spotify_config().get("client_id", "")

    def set_client_id(self, client_id):
        cfg = self.storage.get_spotify_config()
        cfg["client_id"] = client_id.strip()
        self.storage.set_spotify_config(cfg)

    def disconnect(self):
        cfg = self.storage.get_spotify_config()
        cfg["access_token"] = None
        cfg["refresh_token"] = None
        cfg["expires_at"] = 0
        self.storage.set_spotify_config(cfg)

    # ---------- OAuth (PKCE), runs in a background thread ----------
    def start_authorization(self, on_complete):
        """on_complete(success: bool, message: str) is called once the flow
        finishes (or times out), from a background thread — connect it via
        a Qt signal if updating widgets, not directly."""
        client_id = self.client_id()
        if not client_id:
            on_complete(False, "Enter your Spotify Client ID first.")
            return

        verifier, challenge = _generate_pkce_pair()
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

        def worker():
            try:
                server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
            except OSError as e:
                on_complete(False, f"Couldn't start local sign-in listener on port {REDIRECT_PORT}: {e}")
                return

            server.auth_result = None
            webbrowser.open(auth_url)

            deadline = time.time() + 120
            server.timeout = 5
            while server.auth_result is None and time.time() < deadline:
                server.handle_request()
            server.server_close()

            result = server.auth_result
            if not result or not result.get("code"):
                reason = f": {result['error']}" if result and result.get("error") else " (timed out after 2 minutes)"
                on_complete(False, f"Spotify sign-in didn't complete{reason}.")
                return

            ok, msg = self._exchange_code(result["code"], verifier, client_id)
            on_complete(ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _exchange_code(self, code, verifier, client_id):
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        }).encode()
        try:
            req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return False, f"Spotify rejected the sign-in ({e.code}). Double check the Client ID and that {REDIRECT_URI} is registered as a Redirect URI in your Spotify app settings."
        except OSError as e:
            return False, f"Network error talking to Spotify: {e}"

        cfg = self.storage.get_spotify_config()
        cfg["client_id"] = client_id
        cfg["access_token"] = payload["access_token"]
        cfg["refresh_token"] = payload.get("refresh_token", cfg.get("refresh_token"))
        cfg["expires_at"] = time.time() + payload.get("expires_in", 3600) - 30
        self.storage.set_spotify_config(cfg)
        return True, "Connected to Spotify."

    def _ensure_token(self):
        cfg = self.storage.get_spotify_config()
        if not cfg.get("refresh_token"):
            return None
        if cfg.get("access_token") and time.time() < cfg.get("expires_at", 0):
            return cfg["access_token"]

        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": cfg["refresh_token"],
            "client_id": cfg.get("client_id", ""),
        }).encode()
        try:
            req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode())
        except (urllib.error.HTTPError, OSError) as e:
            print(f"[spotify] token refresh failed: {e}")
            return None

        cfg["access_token"] = payload["access_token"]
        if "refresh_token" in payload:
            cfg["refresh_token"] = payload["refresh_token"]
        cfg["expires_at"] = time.time() + payload.get("expires_in", 3600) - 30
        self.storage.set_spotify_config(cfg)
        return cfg["access_token"]

    # ---------- Playback control (blocking calls, short timeouts) ----------
    def _api_request(self, method, path, expect_json=True):
        token = self._ensure_token()
        if not token:
            return None, "Not connected to Spotify"
        req = urllib.request.Request(f"{API_BASE}{path}", method=method)
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read()
                if not expect_json or not raw:
                    return {}, None
                return json.loads(raw), None
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return {}, None
            if e.code == 404:
                return None, "No active Spotify device \u2014 open Spotify somewhere and press play there first."
            if e.code == 401:
                return None, "Spotify session expired \u2014 try reconnecting."
            return None, f"Spotify API error ({e.code})"
        except OSError as e:
            return None, f"Network error: {e}"

    def get_now_playing(self):
        data, err = self._api_request("GET", "/me/player/currently-playing")
        if err:
            return None, err
        if not data or not data.get("item"):
            return None, None
        item = data["item"]
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        return {"title": item.get("name", ""), "artist": artists, "playing": data.get("is_playing", False)}, None

    def get_playback_state(self):
        data, err = self._api_request("GET", "/me/player")
        if err or not data:
            return None, err
        device = data.get("device") or {}
        return {
            "volume": device.get("volume_percent"),
            "shuffle": data.get("shuffle_state"),
            "repeat": data.get("repeat_state"),
        }, None

    def play_pause(self):
        info, err = self.get_now_playing()
        if err:
            return err
        _, err = self._api_request("PUT", "/me/player/pause" if info and info.get("playing") else "/me/player/play")
        return err

    def next_track(self):
        _, err = self._api_request("POST", "/me/player/next")
        return err

    def previous_track(self):
        _, err = self._api_request("POST", "/me/player/previous")
        return err

    def set_volume(self, percent):
        _, err = self._api_request("PUT", f"/me/player/volume?volume_percent={int(percent)}")
        return err

    def set_shuffle(self, on):
        _, err = self._api_request("PUT", f"/me/player/shuffle?state={'true' if on else 'false'}")
        return err

    def set_repeat(self, mode):
        """mode: 'track' | 'context' | 'off'"""
        _, err = self._api_request("PUT", f"/me/player/repeat?state={mode}")
        return err

    # ---------- Async helpers ----------
    # The methods above are blocking network calls. For anything on a
    # timer (polling "now playing" every second or two), block a worker
    # thread instead of the Qt UI thread — pass a Qt signal's .emit as the
    # callback so the result is delivered back on the UI thread safely.

    def run_async(self, fn, callback, *args):
        def worker():
            result = fn(*args)
            callback(result)
        threading.Thread(target=worker, daemon=True).start()

    def get_full_status_async(self, callback):
        """callback(status_dict_or_None, error_or_None). status_dict has
        title/artist/playing (from now-playing) plus volume/shuffle/repeat
        (from playback state), when both calls succeed."""
        def worker():
            info, err = self.get_now_playing()
            if err:
                callback(None, err)
                return
            state, _ = self.get_playback_state()  # non-fatal if this one fails
            combined = dict(info) if info else {}
            if state:
                combined.update(state)
            callback(combined or None, None)
        threading.Thread(target=worker, daemon=True).start()
