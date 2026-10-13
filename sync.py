"""Orchestrates the API client and pure logic. Shared by the GUI and --auto mode.

Create one PlaylistSyncer per user action: it caches the user's playlist list
for the duration of that action to keep API calls low, without going stale
between actions.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import tzinfo
from typing import Callable

import playlist_logic as pl
from playlist_logic import LikedTrack, YearMonth
from spotify_client import SpotifyClient

Log = Callable[[str], None]


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


@dataclass
class SyncResult:
    year: int
    month: int
    name: str
    action: str  # "created" | "updated" | "unchanged" | "skipped"
    added: int = 0
    playlist_id: str | None = None

    def describe(self) -> str:
        if self.action == "created":
            return f"Created '{self.name}' with {plural(self.added, 'track')}."
        if self.action == "updated":
            return f"Updated '{self.name}' with {plural(self.added, 'new track')}."
        if self.action == "unchanged":
            return f"Skipped '{self.name}': already up to date."
        return f"Skipped '{self.name}': no liked songs that month."


@dataclass
class Preview:
    year: int
    month: int
    name: str
    tracks: list[LikedTrack]            # all liked tracks for the month, oldest first
    to_add: list[LikedTrack]            # the subset not yet in the playlist
    existing_playlist: dict | None = None


@dataclass
class BackfillPlan:
    buckets: dict[YearMonth, list[LikedTrack]]
    to_create: list[YearMonth] = field(default_factory=list)
    to_update: list[YearMonth] = field(default_factory=list)

    @property
    def months(self) -> list[YearMonth]:
        return sorted(self.buckets)


class Cancelled(Exception):
    pass


class PlaylistSyncer:
    def __init__(self, client: SpotifyClient, tz: tzinfo | None = None, log: Log | None = None, user: dict | None = None):
        self.client = client
        self.tz = tz
        self.log = log or (lambda _msg: None)
        self._user = user
        self._playlists: list[dict] | None = None

    # ----- cached lookups ---------------------------------------------
    @property
    def user(self) -> dict:
        if self._user is None:
            self._user = self.client.me()
        return self._user

    def owned_playlists(self) -> list[dict]:
        if self._playlists is None:
            uid = self.user["id"]
            self._playlists = [
                p for p in self.client.iter_my_playlists() if p and (p.get("owner") or {}).get("id") == uid
            ]
        return self._playlists

    def find_existing(self, year: int, month: int) -> dict | None:
        return pl.find_owned_playlist(self.owned_playlists(), pl.playlist_name(year, month), self.user["id"])

    def playlist_uris(self, playlist_id: str) -> list[str]:
        return [u for u in (pl.item_uri(e) for e in self.client.iter_playlist_items(playlist_id)) if u]

    # ----- liked songs ------------------------------------------------
    def fetch_liked(self, earliest: YearMonth | None) -> list[LikedTrack]:
        """Liked songs back to the start of `earliest` (local time), or all if None."""
        cutoff = pl.month_start_utc(*earliest, self.tz) if earliest else None
        if earliest:
            self.log(f"Reading Liked Songs back to {pl.playlist_name(*earliest)}...")
        else:
            self.log("Reading all Liked Songs (this can take a while for big libraries)...")
        tracks = pl.collect_since(self.client.iter_saved_tracks(), cutoff)
        self.log(f"Read {len(tracks)} liked songs.")
        return tracks

    def month_buckets(self, earliest: YearMonth | None) -> dict[YearMonth, list[LikedTrack]]:
        return pl.bucket_by_month(self.fetch_liked(earliest), self.tz)

    # ----- operations -------------------------------------------------
    def preview(self, year: int, month: int) -> Preview:
        tracks = self.month_buckets((year, month)).get((year, month), [])
        existing = self.find_existing(year, month)
        to_add = tracks
        if existing and tracks:
            present = set(self.playlist_uris(existing["id"]))
            keep = set(pl.new_uris([t.uri for t in tracks], present))
            to_add = [t for t in tracks if t.uri in keep]
        return Preview(year, month, pl.playlist_name(year, month), tracks, to_add, existing)

    def sync_month(self, year: int, month: int, tracks: list[LikedTrack], public: bool = False) -> SyncResult:
        name = pl.playlist_name(year, month)
        if not tracks:
            result = SyncResult(year, month, name, "skipped")
            self.log(result.describe())
            return result

        desired = [t.uri for t in tracks]
        existing = self.find_existing(year, month)
        if existing:
            to_add = pl.new_uris(desired, self.playlist_uris(existing["id"]))
            if to_add:
                self.client.add_items(existing["id"], to_add)
                result = SyncResult(year, month, name, "updated", len(to_add), existing["id"])
            else:
                result = SyncResult(year, month, name, "unchanged", 0, existing["id"])
        else:
            to_add = pl.new_uris(desired, [])
            created = self.client.create_playlist(name, public, pl.playlist_description(year, month))
            created.setdefault("owner", {"id": self.user["id"]})
            self.owned_playlists().append(created)
            self.client.add_items(created["id"], to_add)
            result = SyncResult(year, month, name, "created", len(to_add), created["id"])
        self.log(result.describe())
        return result

    def sync_one(self, year: int, month: int, public: bool = False) -> SyncResult:
        tracks = self.month_buckets((year, month)).get((year, month), [])
        return self.sync_month(year, month, tracks, public)

    def plan_backfill(self, since: YearMonth | None = None, until: YearMonth | None = None) -> BackfillPlan:
        """Months with liked songs between `since` and `until` (both inclusive; None = unbounded).
        With `since`, Liked Songs are only read back to that month."""
        buckets = self.month_buckets(since)
        if until is not None:
            buckets = {ym: tracks for ym, tracks in buckets.items() if ym <= until}
        plan = BackfillPlan(buckets)
        for ym in plan.months:
            (plan.to_update if self.find_existing(*ym) else plan.to_create).append(ym)
        return plan

    def run_backfill(
        self,
        plan: BackfillPlan,
        public: bool = False,
        progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> list[SyncResult]:
        months = plan.months
        results = []
        for i, ym in enumerate(months, start=1):
            if cancel is not None and cancel.is_set():
                self.log("Backfill cancelled.")
                raise Cancelled()
            results.append(self.sync_month(*ym, plan.buckets[ym], public))
            if progress:
                progress(i, len(months))
        return results

    def auto(self, public: bool = False, now=None) -> list[SyncResult]:
        """Previous month + current month, reading liked songs only as far back as needed."""
        cur = pl.current_month(now, self.tz)
        prev = pl.previous_month(*cur)
        buckets = self.month_buckets(prev)
        return [self.sync_month(*ym, buckets.get(ym, []), public) for ym in (prev, cur)]
