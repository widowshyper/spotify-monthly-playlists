"""Entry point.

  SpotifyMonthlyPlaylists.exe          -> desktop GUI
  SpotifyMonthlyPlaylists.exe --auto   -> headless: sync previous + current month, log to file, exit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import config
from errors import SpotifyAppError

# A windowed (no-console) PyInstaller build has no stdout/stderr; give them a sink
# so argparse/logging never crash on write.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")


def setup_logging(filename: str, console: bool = False) -> str:
    path = config.log_path(filename)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(sh)
    return str(path)


def run_auto(public: bool = False) -> int:
    from auth import SpotifyAuth
    from spotify_client import SpotifyClient
    from sync import PlaylistSyncer

    log = logging.getLogger("auto")
    log.info("=== Auto run started ===")
    cid = config.get_client_id()
    if not cid:
        log.error("No Spotify Client ID configured. Run the app normally once and enter it in Settings.")
        return 2
    try:
        auth = SpotifyAuth(cid)
        if not auth.is_logged_in:
            log.error("Not logged in. Run the app normally once and click Connect.")
            return 2
        client = SpotifyClient(auth, on_wait=log.info)
        syncer = PlaylistSyncer(client, log=log.info)
        results = syncer.auto(public=public)
        log.info("Auto run finished: %s (%d API calls).", "; ".join(r.describe() for r in results), client.calls)
        return 0
    except SpotifyAppError as exc:
        log.error("%s", exc)
        return 1
    except Exception:
        log.exception("Unexpected error during auto run")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create monthly Spotify playlists from your Liked Songs.")
    parser.add_argument("--auto", action="store_true",
                        help="Headless: create/update last month's and this month's playlists, then exit.")
    parser.add_argument("--public", action="store_true",
                        help="With --auto: make newly created playlists public (default: private).")
    args = parser.parse_args(argv)

    if args.auto:
        setup_logging("auto.log", console=True)
        return run_auto(public=args.public)

    setup_logging("app.log")
    from gui import run  # imported lazily so --auto never needs a display
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
