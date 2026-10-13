"""Thin Spotify Web API client built directly on `requests`.

Uses the post-February-2026 endpoints:
  GET  /me, GET /me/tracks, GET /me/playlists
  POST /me/playlists
  GET  /playlists/{id}/items, POST /playlists/{id}/items
"""

from __future__ import annotations

import time
from typing import Callable, Iterable, Iterator

import requests

from auth import SpotifyAuth
from errors import ApiError, AuthError, NetworkError, PremiumRequiredError

API_BASE = "https://api.spotify.com/v1"
ADD_BATCH = 100          # max URIs per POST /playlists/{id}/items
PAGE_LIMIT = 50          # max page size for /me/tracks, /me/playlists, /playlists/{id}/items
MAX_RATE_LIMIT_WAIT = 600  # seconds; longer Retry-After values abort instead of hanging
MAX_RETRIES = 5


class SpotifyClient:
    def __init__(
        self,
        auth: SpotifyAuth,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_wait: Callable[[str], None] | None = None,
    ):
        self.auth = auth
        self.session = session or requests.Session()
        self.sleep = sleep
        self.on_wait = on_wait  # optional hook to report rate-limit waits to the UI
        self.calls = 0

    # ----- core request loop ------------------------------------------
    def _request(self, method: str, path_or_url: str, params: dict | None = None, json: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else API_BASE + path_or_url
        refreshed = False
        attempt = 0
        while True:
            token = self.auth.get_access_token()
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt < 2:
                    attempt += 1
                    self.sleep(2 ** attempt)
                    continue
                raise NetworkError("Network error: could not reach Spotify. Check your internet connection.") from exc
            self.calls += 1

            status = resp.status_code
            if status == 429:
                wait = _retry_after_seconds(resp)
                if wait > MAX_RATE_LIMIT_WAIT or attempt >= MAX_RETRIES:
                    raise ApiError(
                        f"Spotify rate limit reached (asked to wait {wait}s). Please try again later.", status
                    )
                attempt += 1
                if self.on_wait:
                    self.on_wait(f"Rate limited by Spotify, waiting {wait}s...")
                self.sleep(wait)
                continue
            if status == 401 and not refreshed:
                # Token may have been revoked/expired early; refresh once and retry.
                self.auth.get_access_token(force_refresh=True)
                refreshed = True
                continue
            if status in (500, 502, 503, 504) and attempt < 3:
                attempt += 1
                self.sleep(2 ** attempt)
                continue
            if 200 <= status < 300:
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {}
            raise _to_error(resp)

    def _paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """Yield items across pages, following `next`. Stop iterating to stop fetching."""
        page = self._request("GET", path, params=params)
        while True:
            yield from page.get("items") or []
            nxt = page.get("next")
            if not nxt:
                return
            page = self._request("GET", nxt)

    # ----- endpoints --------------------------------------------------
    def me(self) -> dict:
        return self._request("GET", "/me")

    def iter_saved_tracks(self) -> Iterator[dict]:
        """Liked Songs, newest first. Each item has `added_at` and the track object."""
        return self._paginate("/me/tracks", {"limit": PAGE_LIMIT})

    def iter_my_playlists(self) -> Iterator[dict]:
        return self._paginate("/me/playlists", {"limit": PAGE_LIMIT})

    def iter_playlist_items(self, playlist_id: str) -> Iterator[dict]:
        return self._paginate(f"/playlists/{playlist_id}/items", {"limit": PAGE_LIMIT})

    def create_playlist(self, name: str, public: bool, description: str) -> dict:
        return self._request(
            "POST",
            "/me/playlists",
            json={"name": name, "public": public, "description": description},
        )

    def add_items(self, playlist_id: str, uris: Iterable[str]) -> int:
        """Append URIs in order, in batches of 100. Returns the number added."""
        uris = list(uris)
        for i in range(0, len(uris), ADD_BATCH):
            self._request("POST", f"/playlists/{playlist_id}/items", json={"uris": uris[i : i + ADD_BATCH]})
        return len(uris)


def _retry_after_seconds(resp: requests.Response) -> int:
    try:
        return max(1, int(float(resp.headers.get("Retry-After", "5"))))
    except ValueError:
        return 5


def _to_error(resp: requests.Response) -> Exception:
    status = resp.status_code
    message = ""
    try:
        body = resp.json()
        err = body.get("error")
        message = err.get("message", "") if isinstance(err, dict) else str(err or "")
    except ValueError:
        message = resp.text[:200]

    if status == 401:
        return AuthError("Your Spotify session has expired. Please click Connect to log in again.")
    if status == 403:
        if "premium" in message.lower():
            return PremiumRequiredError(
                "Spotify Premium subscription required. Apps in Development Mode only work "
                "while the app owner's account has an active Premium subscription."
            )
        return ApiError(
            f"Spotify refused the request (403: {message or 'Forbidden'}). If your app is in "
            "Development Mode, make sure your Spotify account is added under User Management "
            "in the Developer Dashboard, then disconnect and connect again.",
            status,
        )
    if status == 404:
        return ApiError(f"Spotify could not find that resource (404: {message or 'Not found'}).", status)
    return ApiError(f"Spotify API error {status}: {message or 'unknown error'}", status)
