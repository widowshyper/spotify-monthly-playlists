"""Spotify Authorization Code flow with PKCE, plus a cached, auto-refreshing token.

No client secret is used or stored. Tokens are cached in the user's app-data
folder (see config.token_path()).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

import config
from errors import AuthError, ConfigError, NetworkError, NotLoggedInError

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
# Refresh this many seconds before the token actually expires.
EXPIRY_MARGIN = 60

_SUCCESS_PAGE = b"""<!doctype html><html><head><meta charset="utf-8">
<title>Spotify Monthly Playlists</title></head>
<body style="font-family:sans-serif;text-align:center;margin-top:4em">
<h2>You're connected.</h2><p>You can close this tab and return to the app.</p>
</body></html>"""

_FAIL_PAGE = b"""<!doctype html><html><head><meta charset="utf-8">
<title>Spotify Monthly Playlists</title></head>
<body style="font-family:sans-serif;text-align:center;margin-top:4em">
<h2>Login was not completed.</h2><p>Return to the app for details.</p>
</body></html>"""


def generate_code_verifier() -> str:
    # 64 random bytes -> 86 URL-safe chars (spec allows 43-128).
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        if parsed.path != urlparse(config.REDIRECT_URI).path:
            self.send_response(404)
            self.end_headers()
            return
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.result = params  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_SUCCESS_PAGE if "code" in params else _FAIL_PAGE)

    def log_message(self, format, *args):  # silence default stderr logging
        pass


def _wait_for_callback(
    auth_url: str,
    open_browser: Callable[[str], object],
    timeout: float,
    cancel: threading.Event | None,
) -> dict:
    redirect = urlparse(config.REDIRECT_URI)
    try:
        server = HTTPServer((redirect.hostname, redirect.port), _CallbackHandler)
    except OSError as exc:
        raise AuthError(
            f"Could not listen on {redirect.hostname}:{redirect.port} for the login "
            f"callback (is another copy of the app running?). Details: {exc}"
        ) from exc
    server.result = None  # type: ignore[attr-defined]
    server.timeout = 0.5
    try:
        open_browser(auth_url)
        deadline = time.monotonic() + timeout
        while server.result is None:  # type: ignore[attr-defined]
            if cancel is not None and cancel.is_set():
                raise AuthError("Login cancelled.")
            if time.monotonic() > deadline:
                raise AuthError("Timed out waiting for Spotify login in the browser.")
            server.handle_request()
        return server.result  # type: ignore[attr-defined]
    finally:
        server.server_close()


class SpotifyAuth:
    """Owns the cached token and hands out valid access tokens."""

    def __init__(self, client_id: str, token_file: Path | None = None, session: requests.Session | None = None):
        if not client_id:
            raise ConfigError("No Spotify Client ID configured. Open Settings and enter your Client ID.")
        self.client_id = client_id
        self.token_file = token_file or config.token_path()
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._token: dict | None = self._load()

    # ----- token cache -------------------------------------------------
    def _load(self) -> dict | None:
        try:
            data = json.loads(self.token_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        # A token issued for a different Client ID can't be refreshed with this one.
        if data.get("client_id") != self.client_id or not data.get("refresh_token"):
            return None
        return data

    def _save(self, token: dict) -> None:
        self._token = token
        config.write_private(self.token_file, token)

    @property
    def is_logged_in(self) -> bool:
        return self._token is not None

    def logout(self) -> None:
        with self._lock:
            self._token = None
            try:
                self.token_file.unlink()
            except FileNotFoundError:
                pass

    # ----- interactive login ------------------------------------------
    def login(
        self,
        open_browser: Callable[[str], object] = webbrowser.open,
        timeout: float = 300,
        cancel: threading.Event | None = None,
    ) -> None:
        verifier = generate_code_verifier()
        state = secrets.token_urlsafe(16)
        auth_url = AUTHORIZE_URL + "?" + urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": config.REDIRECT_URI,
                "code_challenge_method": "S256",
                "code_challenge": code_challenge(verifier),
                "scope": " ".join(config.SCOPES),
                "state": state,
            }
        )
        result = _wait_for_callback(auth_url, open_browser, timeout, cancel)

        if result.get("state") != state:
            raise AuthError("Login failed: state mismatch (possible stale or forged callback). Try again.")
        if "error" in result:
            if result["error"] == "access_denied":
                raise AuthError("Login cancelled: access was denied in the browser.")
            raise AuthError(f"Spotify login error: {result['error']}")
        if "code" not in result:
            raise AuthError("Login failed: no authorization code received.")

        token = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": result["code"],
                "redirect_uri": config.REDIRECT_URI,
                "client_id": self.client_id,
                "code_verifier": verifier,
            }
        )
        with self._lock:
            self._save(self._normalise(token, previous_refresh=None))

    # ----- access tokens ----------------------------------------------
    def get_access_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            if self._token is None:
                raise NotLoggedInError("Not connected to Spotify. Click Connect to log in.")
            if force_refresh or time.time() >= self._token.get("expires_at", 0):
                self._refresh()
            return self._token["access_token"]

    def _refresh(self) -> None:
        assert self._token is not None
        old_refresh = self._token["refresh_token"]
        try:
            token = self._token_request(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": old_refresh,
                    "client_id": self.client_id,
                }
            )
        except AuthError:
            # Refresh token revoked/expired: forget it so the UI prompts a new login.
            self._token = None
            try:
                self.token_file.unlink()
            except FileNotFoundError:
                pass
            raise
        self._save(self._normalise(token, previous_refresh=old_refresh))

    def _normalise(self, token: dict, previous_refresh: str | None) -> dict:
        return {
            "client_id": self.client_id,
            "access_token": token["access_token"],
            # Spotify may or may not rotate the refresh token; keep the old one if not.
            "refresh_token": token.get("refresh_token") or previous_refresh,
            "expires_at": time.time() + int(token.get("expires_in", 3600)) - EXPIRY_MARGIN,
            "scope": token.get("scope", ""),
        }

    def _token_request(self, data: dict) -> dict:
        try:
            resp = self.session.post(TOKEN_URL, data=data, timeout=20)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise NetworkError("Network error: could not reach Spotify. Check your internet connection.") from exc
        if resp.status_code == 200:
            return resp.json()
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = body.get("error", "")
        desc = body.get("error_description", "") or resp.text[:200]
        if err == "invalid_client":
            raise ConfigError(
                "Spotify rejected the Client ID. Check it in Settings, and make sure "
                f"{config.REDIRECT_URI} is added as a Redirect URI in your Spotify app."
            )
        if err == "invalid_grant":
            raise AuthError("Your Spotify session has expired or was revoked. Please click Connect to log in again.")
        raise AuthError(f"Spotify token request failed ({resp.status_code}): {err or ''} {desc}".strip())
