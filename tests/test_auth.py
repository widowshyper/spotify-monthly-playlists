import json
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

import auth
from errors import AuthError, NotLoggedInError


def test_code_challenge_matches_rfc7636_example():
    # RFC 7636 Appendix B
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert auth.code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_code_verifier_length_within_spec():
    v = auth.generate_code_verifier()
    assert 43 <= len(v) <= 128


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append(data)
        return self.responses.pop(0)


def test_login_round_trip_caches_token(tmp_path):
    session = FakeSession([FakeResp(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})])
    a = auth.SpotifyAuth("cid", token_file=tmp_path / "token.json", session=session)

    def fake_browser(url):
        q = parse_qs(urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"]
        assert q["redirect_uri"] == ["http://127.0.0.1:8888/callback"]
        state = q["state"][0]

        def hit():
            time.sleep(0.2)
            urllib.request.urlopen(f"http://127.0.0.1:8888/callback?code=XYZ&state={state}").read()

        threading.Thread(target=hit, daemon=True).start()

    a.login(open_browser=fake_browser, timeout=10)
    assert a.get_access_token() == "A1"
    assert session.calls[0]["code"] == "XYZ"
    assert "code_verifier" in session.calls[0]
    assert "client_secret" not in session.calls[0]
    saved = json.loads((tmp_path / "token.json").read_text())
    assert saved["refresh_token"] == "R1"

    # A fresh instance picks up the cached token without logging in again.
    b = auth.SpotifyAuth("cid", token_file=tmp_path / "token.json", session=session)
    assert b.is_logged_in


def test_expired_token_refreshes_and_keeps_old_refresh_token(tmp_path):
    tf = tmp_path / "token.json"
    tf.write_text(json.dumps({"client_id": "cid", "access_token": "old", "refresh_token": "R1", "expires_at": 0}))
    session = FakeSession([FakeResp(200, {"access_token": "new", "expires_in": 3600})])
    a = auth.SpotifyAuth("cid", token_file=tf, session=session)
    assert a.get_access_token() == "new"
    assert session.calls[0]["grant_type"] == "refresh_token"
    assert json.loads(tf.read_text())["refresh_token"] == "R1"


def test_revoked_refresh_token_clears_cache(tmp_path):
    tf = tmp_path / "token.json"
    tf.write_text(json.dumps({"client_id": "cid", "access_token": "old", "refresh_token": "R1", "expires_at": 0}))
    session = FakeSession([FakeResp(400, {"error": "invalid_grant"})])
    a = auth.SpotifyAuth("cid", token_file=tf, session=session)
    with pytest.raises(AuthError):
        a.get_access_token()
    assert not tf.exists()
    with pytest.raises(NotLoggedInError):
        a.get_access_token()


def test_token_for_other_client_id_is_ignored(tmp_path):
    tf = tmp_path / "token.json"
    tf.write_text(json.dumps({"client_id": "other", "access_token": "x", "refresh_token": "r", "expires_at": 9e12}))
    assert not auth.SpotifyAuth("cid", token_file=tf, session=FakeSession([])).is_logged_in
