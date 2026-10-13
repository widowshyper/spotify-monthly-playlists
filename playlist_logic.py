"""Pure functions: parsing liked-song items, bucketing them into local months,
and working out which tracks a playlist is missing. No I/O here.

Timezone handling: every function that needs "local time" takes an optional
`tz`. When `tz` is None the system's local timezone rules are used *for each
instant* (via datetime.astimezone()), so DST transitions are handled correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Iterable

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

YearMonth = tuple[int, int]


@dataclass(frozen=True)
class LikedTrack:
    uri: str
    name: str
    artists: str
    added_at: datetime  # timezone-aware, UTC


# ----- parsing -----------------------------------------------------------

def parse_added_at(value: str) -> datetime:
    """Parse Spotify's ISO-8601 UTC timestamp (e.g. '2026-10-31T22:00:00Z')."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def item_object(entry: dict) -> dict | None:
    """The track/episode inside a saved-track or playlist entry.

    Post-Feb-2026 playlist entries use `item`; older shapes use `track`.
    """
    obj = entry.get("item")
    if obj is None:
        obj = entry.get("track")
    return obj if isinstance(obj, dict) else None


def item_uri(entry: dict) -> str | None:
    """A usable Spotify URI for an entry, or None for local files / null items."""
    if entry.get("is_local"):
        return None
    obj = item_object(entry)
    if not obj or obj.get("is_local"):
        return None
    uri = obj.get("uri")
    if not uri or uri.startswith("spotify:local:"):
        return None
    return uri


def parse_saved_item(entry: dict) -> LikedTrack | None:
    uri = item_uri(entry)
    added = entry.get("added_at")
    if uri is None or not added:
        return None
    obj = item_object(entry) or {}
    artists = ", ".join(a.get("name", "") for a in obj.get("artists") or [] if a)
    return LikedTrack(uri=uri, name=obj.get("name") or "(unknown)", artists=artists, added_at=parse_added_at(added))


# ----- months ------------------------------------------------------------

def to_local(dt_utc: datetime, tz: tzinfo | None = None) -> datetime:
    return dt_utc.astimezone(tz) if tz is not None else dt_utc.astimezone()


def local_month(dt_utc: datetime, tz: tzinfo | None = None) -> YearMonth:
    local = to_local(dt_utc, tz)
    return (local.year, local.month)


def month_start_utc(year: int, month: int, tz: tzinfo | None = None) -> datetime:
    """The UTC instant of local midnight on the 1st of the given month."""
    if tz is not None:
        local = datetime(year, month, 1, tzinfo=tz)
    else:
        local = datetime(year, month, 1).astimezone()  # naive -> system local
    return local.astimezone(timezone.utc)


def previous_month(year: int, month: int) -> YearMonth:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def current_month(now: datetime | None = None, tz: tzinfo | None = None) -> YearMonth:
    now = now or datetime.now(timezone.utc)
    return local_month(now, tz)


def playlist_name(year: int, month: int) -> str:
    return f"{MONTH_NAMES[month - 1]} {year}"


def playlist_description(year: int, month: int) -> str:
    return f"Liked songs from {playlist_name(year, month)}. Auto-generated."


def bucket_by_month(tracks: Iterable[LikedTrack], tz: tzinfo | None = None) -> dict[YearMonth, list[LikedTrack]]:
    """Group tracks by local (year, month); each list sorted oldest-liked first."""
    buckets: dict[YearMonth, list[LikedTrack]] = {}
    for t in tracks:
        buckets.setdefault(local_month(t.added_at, tz), []).append(t)
    for lst in buckets.values():
        lst.sort(key=lambda t: t.added_at)
    return buckets


def collect_since(entries: Iterable[dict], cutoff_utc: datetime | None) -> list[LikedTrack]:
    """Parse liked-song entries (newest first), stopping at the first entry older
    than `cutoff_utc`. Because `entries` may be a lazy paginator, stopping here
    also stops further API calls. Pass cutoff None to read everything."""
    out: list[LikedTrack] = []
    for entry in entries:
        added = entry.get("added_at")
        if cutoff_utc is not None and added and parse_added_at(added) < cutoff_utc:
            break
        track = parse_saved_item(entry)
        if track is not None:
            out.append(track)
    return out


# ----- playlists ---------------------------------------------------------

def find_owned_playlist(playlists: Iterable[dict], name: str, user_id: str) -> dict | None:
    """First playlist owned by `user_id` whose name matches exactly."""
    for p in playlists:
        if p and p.get("name") == name and (p.get("owner") or {}).get("id") == user_id:
            return p
    return None


def new_uris(desired: Iterable[str], existing: Iterable[str]) -> list[str]:
    """URIs from `desired` not already in `existing`, order kept, no repeats."""
    seen = set(existing)
    out = []
    for uri in desired:
        if uri not in seen:
            seen.add(uri)
            out.append(uri)
    return out
