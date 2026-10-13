import json

import pytest

from errors import ApiError, PremiumRequiredError
from spotify_client import SpotifyClient


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.content = json.dumps(self._body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.requests.append((method, url, params, json))
        return self.responses.pop(0)


class Auth:
    def __init__(self):
        self.forced = 0

    def get_access_token(self, force_refresh=False):
        self.forced += force_refresh
        return "tok"


def make(responses):
    sleeps = []
    session = Session(responses)
    auth = Auth()
    return SpotifyClient(auth, session=session, sleep=sleeps.append), session, sleeps, auth


def test_429_honours_retry_after_then_succeeds():
    client, session, sleeps, _ = make([Resp(429, headers={"Retry-After": "7"}), Resp(200, {"id": "me"})])
    assert client.me() == {"id": "me"}
    assert sleeps == [7]
    assert len(session.requests) == 2


def test_huge_retry_after_aborts_instead_of_hanging():
    client, _, sleeps, _ = make([Resp(429, headers={"Retry-After": "86400"})])
    with pytest.raises(ApiError):
        client.me()
    assert sleeps == []


def test_401_forces_one_refresh_and_retries():
    client, _, _, auth = make([Resp(401), Resp(200, {"id": "me"})])
    assert client.me()["id"] == "me"
    assert auth.forced == 1


def test_pagination_follows_next_and_stops_lazily():
    p1 = {"items": [1, 2], "next": "https://api.spotify.com/v1/me/tracks?offset=2&limit=50"}
    p2 = {"items": [3], "next": None}
    client, session, _, _ = make([Resp(200, p1), Resp(200, p2)])
    it = client.iter_saved_tracks()
    assert next(it) == 1
    assert len(session.requests) == 1  # second page not fetched until needed
    assert list(it) == [2, 3]
    assert session.requests[0][1].endswith("/me/tracks")
    assert session.requests[0][2] == {"limit": 50}
    assert session.requests[1][1] == p1["next"]


def test_add_items_batches_by_100_in_order():
    uris = [f"spotify:track:{i}" for i in range(250)]
    client, session, _, _ = make([Resp(201, {"snapshot_id": "x"})] * 3)
    assert client.add_items("pl", uris) == 250
    sent = [r[3]["uris"] for r in session.requests]
    assert [len(b) for b in sent] == [100, 100, 50]
    assert sum(sent, []) == uris
    assert all(r[1].endswith("/playlists/pl/items") for r in session.requests)


def test_create_playlist_uses_me_endpoint():
    client, session, _, _ = make([Resp(201, {"id": "new"})])
    client.create_playlist("October 2026", False, "desc")
    method, url, _, body = session.requests[0]
    assert (method, url) == ("POST", "https://api.spotify.com/v1/me/playlists")
    assert body == {"name": "October 2026", "public": False, "description": "desc"}


def test_403_premium_maps_to_friendly_error():
    client, _, _, _ = make([Resp(403, {"error": {"status": 403, "message": "Premium required"}})])
    with pytest.raises(PremiumRequiredError):
        client.me()
