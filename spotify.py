"""Spotify control through the official Web API (requires Spotify Premium for playback).

Sign-in uses OAuth with PKCE: no client secret exists in the app, the browser login happens on
Spotify's own site, and only a short list of playback/playlist permissions is requested. The
tokens are stored encrypted with DPAPI.
"""
import base64
import difflib
import functools
import hashlib
import http.server
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import config
import securestore

log = logging.getLogger("jarvis.spotify")

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = ("user-read-playback-state user-modify-playback-state user-read-currently-playing "
          "playlist-read-private playlist-read-collaborative")
API = "https://api.spotify.com/v1"
NOT_CONNECTED = ("Spotify isn't connected yet. Tell the user to right-click the orb and choose "
                 "'Connect Spotify'.")

LIKED_SONGS = re.compile(r"\b(liked?|likes|favou?rites?|saved)\s*(songs?|tracks?|music)?\b")

_lock = threading.Lock()


class SpotifyError(Exception):
    pass


# ---------------------------------------------------------------- auth

def client_id():
    return securestore.get_setting("spotify_client_id")


def is_connected():
    return bool(client_id()) and bool(securestore.load(config.SPOTIFY_TOKEN_FILE, {}).get("refresh_token"))


def _token_request(params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        token = json.loads(resp.read())
    old = securestore.load(config.SPOTIFY_TOKEN_FILE, {})
    token["refresh_token"] = token.get("refresh_token") or old.get("refresh_token")
    token["expires_at"] = time.time() + token.get("expires_in", 3600) - 60
    securestore.save(config.SPOTIFY_TOKEN_FILE, token)
    return token


def connect(on_done):
    """Runs the browser sign-in. Calls on_done(success: bool, message: str) when finished."""
    cid = client_id()
    if not cid:
        on_done(False, "No Spotify client ID set.")
        return
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(url.query)
            ok = query.get("state", [""])[0] == state and "code" in query  # state check blocks CSRF
            result["code"] = query["code"][0] if ok else None
            result["error"] = query.get("error", ["state mismatch" if not ok else ""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Spotify connected. You can close this tab." if ok else "Spotify connection failed."
            self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'>{msg}</body></html>".encode())

        def log_message(self, *args):
            pass

    def run():
        try:
            server = http.server.HTTPServer(("127.0.0.1", 8888), Handler)  # loopback only
        except OSError:
            on_done(False, "Port 8888 is in use, so Spotify sign-in can't receive its callback.")
            return
        server.timeout = 1
        webbrowser.open("https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
            "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT_URI, "scope": SCOPES,
            "state": state, "code_challenge_method": "S256", "code_challenge": challenge}))
        deadline = time.time() + 300
        while "code" not in result and time.time() < deadline:
            server.handle_request()
        server.server_close()
        if not result.get("code"):
            on_done(False, f"Spotify sign-in didn't complete ({result.get('error') or 'timed out'}).")
            return
        try:
            _token_request({"grant_type": "authorization_code", "code": result["code"],
                            "redirect_uri": REDIRECT_URI, "client_id": cid, "code_verifier": verifier})
            on_done(True, "Spotify connected.")
        except Exception as e:
            log.exception("Spotify token exchange failed")
            on_done(False, f"Spotify sign-in failed: {e}")

    threading.Thread(target=run, daemon=True, name="spotify-auth").start()


def disconnect():
    if os.path.exists(config.SPOTIFY_TOKEN_FILE):
        os.remove(config.SPOTIFY_TOKEN_FILE)


def _access_token():
    token = securestore.load(config.SPOTIFY_TOKEN_FILE, {})
    if not token.get("refresh_token") or not client_id():
        raise SpotifyError(NOT_CONNECTED)
    if time.time() >= token.get("expires_at", 0):
        token = _token_request({"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                                "client_id": client_id()})
    return token["access_token"]


# ---------------------------------------------------------------- API

def _api(method, path, params=None, body=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        if e.code == 404 and "NO_ACTIVE_DEVICE" in detail:
            raise SpotifyError("NO_ACTIVE_DEVICE")
        if e.code == 403 and "PREMIUM" in detail.upper():
            raise SpotifyError("Spotify playback control needs a Premium account.")
        raise SpotifyError(f"Spotify error {e.code}: {detail[:200]}")


def _device_id():
    """An available playback device, launching the Spotify app if none is running."""
    devices = _api("GET", "/me/player/devices").get("devices", [])
    if not devices:
        os.startfile("spotify:")
        for _ in range(15):
            time.sleep(1)
            devices = _api("GET", "/me/player/devices").get("devices", [])
            if devices:
                break
    if not devices:
        raise SpotifyError("No Spotify device found. Open Spotify on this PC and try again.")
    active = [d for d in devices if d.get("is_active")]
    computer = [d for d in devices if d.get("type") == "Computer"]
    return (active or computer or devices)[0]["id"]


def _player(method, path, params=None, body=None):
    """Player call that recovers when no device is active."""
    try:
        return _api(method, path, params, body)
    except SpotifyError as e:
        if str(e) != "NO_ACTIVE_DEVICE":
            raise
        return _api(method, path, {**(params or {}), "device_id": _device_id()}, body)


def _wrap(func):
    @functools.wraps(func)  # keeps the signature and docstring the LLM tool schema is built from
    def inner(*args, **kwargs):
        with _lock:
            try:
                return func(*args, **kwargs)
            except SpotifyError as e:
                return str(e)
            except Exception as e:
                log.exception("Spotify call failed")
                return f"Spotify request failed: {e}"
    return inner


def _describe(item):
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    return f"{item['name']} by {artists}" if artists else item["name"]


@_wrap
def spotify_play(query: str, kind: str = "track") -> str:
    """Play a specific song, album, artist or playlist on Spotify.

    Args:
        query: What to play, e.g. "Blinding Lights The Weeknd", "my workout playlist", "Daft Punk".
        kind: One of "track", "album", "artist", "playlist". Use "playlist" for the user's own playlists.
    """
    kind = kind if kind in ("track", "album", "artist", "playlist") else "track"
    q = re.sub(r"\b(my|the|playlist|on spotify|spotify)\b", " ", query.lower())
    q = " ".join(re.sub(r"[^\w\s.]", " ", q).split())
    wants_liked = bool(LIKED_SONGS.search(q))
    if kind == "playlist" or wants_liked:
        mine = _api("GET", "/me/playlists", {"limit": 50}).get("items", [])
        names = {p["name"].lower().strip(): p for p in mine if p}
        match = names.get(q)  # an exact name always wins (e.g. a playlist called "Liked 2.0")
        if not match and wants_liked:
            user_id = _api("GET", "/me")["id"]
            _player("PUT", "/me/player/play", body={"context_uri": f"spotify:user:{user_id}:collection"})
            return "Playing your Liked Songs."
        if not match:
            match = next((p for n, p in names.items() if q and q in n), None)
        if not match:
            close = difflib.get_close_matches(q, names.keys(), n=1, cutoff=0.75)
            match = names[close[0]] if close else None
        if match:
            _player("PUT", "/me/player/play", body={"context_uri": match["uri"]})
            return f"Playing your playlist {match['name']}."
        if "my" in query.lower().split():
            listing = ", ".join(p["name"] for p in mine if p)
            return f"You don't have a playlist called {query}. Your playlists are: {listing}."
    results = _api("GET", "/search", {"q": query, "type": kind, "limit": 1})
    items = [i for i in results.get(kind + "s", {}).get("items", []) if i]
    if not items:
        return f"Couldn't find {query} on Spotify."
    item = items[0]
    body = {"uris": [item["uri"]]} if kind == "track" else {"context_uri": item["uri"]}
    _player("PUT", "/me/player/play", body=body)
    return f"Playing {_describe(item)}."


@_wrap
def spotify_queue(query: str) -> str:
    """Add a song to the Spotify queue to play next.

    Args:
        query: The song, e.g. "Bohemian Rhapsody Queen".
    """
    items = [i for i in _api("GET", "/search", {"q": query, "type": "track", "limit": 1})
             .get("tracks", {}).get("items", []) if i]
    if not items:
        return f"Couldn't find {query} on Spotify."
    _player("POST", "/me/player/queue", {"uri": items[0]["uri"]})
    return f"Queued {_describe(items[0])}."


@_wrap
def spotify_control(action: str) -> str:
    """Control Spotify playback.

    Args:
        action: One of "pause", "resume", "next", "previous", "shuffle_on", "shuffle_off", "repeat_on", "repeat_off".
    """
    calls = {
        "pause": ("PUT", "/me/player/pause", None), "resume": ("PUT", "/me/player/play", None),
        "next": ("POST", "/me/player/next", None), "previous": ("POST", "/me/player/previous", None),
        "shuffle_on": ("PUT", "/me/player/shuffle", {"state": "true"}),
        "shuffle_off": ("PUT", "/me/player/shuffle", {"state": "false"}),
        "repeat_on": ("PUT", "/me/player/repeat", {"state": "context"}),
        "repeat_off": ("PUT", "/me/player/repeat", {"state": "off"}),
    }
    if action not in calls:
        return f"Unknown action {action}."
    method, path, params = calls[action]
    _player(method, path, params)
    return f"Spotify: {action.replace('_', ' ')}."


@_wrap
def spotify_now_playing() -> str:
    """Say what song is currently playing on Spotify."""
    data = _api("GET", "/me/player/currently-playing")
    item = data.get("item") if data else None
    if not item:
        return "Nothing is playing on Spotify."
    state = "playing" if data.get("is_playing") else "paused"
    return f"{_describe(item)} ({state})."
