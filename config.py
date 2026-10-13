"""Per-user storage locations and the app config (Spotify Client ID).

Everything lives in the user's app-data folder, never in the project folder:
  Windows: %APPDATA%\\SpotifyMonthlyPlaylists
  macOS:   ~/Library/Application Support/SpotifyMonthlyPlaylists
  Linux:   $XDG_CONFIG_HOME/SpotifyMonthlyPlaylists (default ~/.config)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "SpotifyMonthlyPlaylists"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = [
    "user-library-read",
    "playlist-read-private",
    "playlist-modify-private",
    "playlist-modify-public",
]


def app_dir() -> Path:
    """Return (and create) the per-user data directory."""
    override = os.environ.get("SMP_APP_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        base = Path(xdg) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return app_dir() / "config.json"


def token_path() -> Path:
    return app_dir() / "token.json"


def log_path(name: str = "app.log") -> Path:
    return app_dir() / name


def write_private(path: Path, data: dict) -> None:
    """Write JSON atomically, readable only by the current user where supported."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    write_private(config_path(), cfg)


def get_client_id() -> str | None:
    """Client ID from the SPOTIFY_CLIENT_ID env var, else the config file."""
    env = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    if env:
        return env
    cid = str(load_config().get("client_id", "")).strip()
    return cid or None


def set_client_id(client_id: str) -> None:
    cfg = load_config()
    cfg["client_id"] = client_id.strip()
    save_config(cfg)
