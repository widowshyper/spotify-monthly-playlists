import pytest


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    """Never touch the real user's app-data folder during tests."""
    monkeypatch.setenv("SMP_APP_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
