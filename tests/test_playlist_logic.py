from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import playlist_logic as pl

HOBART = ZoneInfo("Australia/Hobart")
NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc


def entry(added_at, uri="spotify:track:x", name="Song", artists=("Artist",), key="track", is_local=False):
    obj = {"uri": uri, "name": name, "artists": [{"name": a} for a in artists], "is_local": is_local}
    return {"added_at": added_at, key: obj}


def lt(added_at, uri):
    return pl.LikedTrack(uri=uri, name="n", artists="a", added_at=pl.parse_added_at(added_at))


# ----- parsing ---------------------------------------------------------------

def test_parse_added_at_is_aware_utc():
    dt = pl.parse_added_at("2026-10-31T22:00:00Z")
    assert dt == datetime(2026, 10, 31, 22, 0, tzinfo=UTC)
    assert dt.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("key", ["track", "item"])
def test_parse_saved_item_accepts_track_or_item_key(key):
    t = pl.parse_saved_item(entry("2026-10-01T00:00:00Z", key=key, artists=("A", "B")))
    assert t.uri == "spotify:track:x"
    assert t.artists == "A, B"


@pytest.mark.parametrize(
    "e",
    [
        {"added_at": "2026-10-01T00:00:00Z", "track": None},
        {"added_at": "2026-10-01T00:00:00Z", "item": None},
        {"added_at": "2026-10-01T00:00:00Z", "track": {"uri": None, "name": "x"}},
        {"added_at": "2026-10-01T00:00:00Z", "is_local": True, "track": {"uri": "spotify:local:a:b:c:1"}},
        {"added_at": "2026-10-01T00:00:00Z", "track": {"uri": "spotify:local:a:b:c:1", "is_local": True}},
    ],
)
def test_local_files_and_null_items_are_skipped(e):
    assert pl.parse_saved_item(e) is None
    assert pl.item_uri(e) is None


# ----- timezone & month boundaries --------------------------------------------

def test_tasmania_morning_of_1_nov_is_november_not_october():
    # 9am 1 Nov in Hobart (AEDT, UTC+11) == 22:00 UTC on 31 Oct
    dt = pl.parse_added_at("2026-10-31T22:00:00Z")
    assert pl.local_month(dt, UTC) == (2026, 10)
    assert pl.local_month(dt, HOBART) == (2026, 11)


def test_new_york_evening_of_31_oct_is_october_not_november():
    # 9pm 31 Oct in New York (EDT, UTC-4) == 01:00 UTC on 1 Nov
    dt = pl.parse_added_at("2026-11-01T01:00:00Z")
    assert pl.local_month(dt, UTC) == (2026, 11)
    assert pl.local_month(dt, NEW_YORK) == (2026, 10)


def test_new_year_boundary():
    dt = pl.parse_added_at("2026-12-31T14:30:00Z")  # 01:30 1 Jan 2027 in Hobart
    assert pl.local_month(dt, HOBART) == (2027, 1)
    assert pl.local_month(dt, UTC) == (2026, 12)


def test_exact_midnight_belongs_to_new_month():
    assert pl.local_month(datetime(2026, 11, 1, tzinfo=UTC), UTC) == (2026, 11)
    assert pl.local_month(datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC), UTC) == (2026, 10)


def test_month_start_utc_respects_dst():
    # Hobart: DST starts first Sunday of October, so 1 Oct is still AEST (+10)
    assert pl.month_start_utc(2026, 10, HOBART) == datetime(2026, 9, 30, 14, 0, tzinfo=UTC)
    # ...and 1 Nov is AEDT (+11)
    assert pl.month_start_utc(2026, 11, HOBART) == datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
    assert pl.month_start_utc(2026, 3, NEW_YORK) == datetime(2026, 3, 1, 5, 0, tzinfo=UTC)


def test_month_start_utc_system_local_is_consistent():
    start = pl.month_start_utc(2026, 7)
    assert start.tzinfo is not None
    assert pl.local_month(start) == (2026, 7)
    assert pl.local_month(start - timedelta(seconds=1)) == (2026, 6)


@pytest.mark.parametrize("ym,expected", [((2026, 1), (2025, 12)), ((2026, 3), (2026, 2)), ((2026, 12), (2026, 11))])
def test_previous_month(ym, expected):
    assert pl.previous_month(*ym) == expected


def test_current_month_uses_local_tz():
    now = datetime(2026, 10, 31, 22, 0, tzinfo=UTC)
    assert pl.current_month(now, HOBART) == (2026, 11)


def test_names_and_description():
    assert pl.playlist_name(2026, 10) == "October 2026"
    assert pl.playlist_name(2027, 1) == "January 2027"
    assert pl.playlist_description(2026, 10) == "Liked songs from October 2026. Auto-generated."


# ----- bucketing ---------------------------------------------------------------

def test_bucket_by_month_sorts_oldest_first_and_uses_local_tz():
    tracks = [  # newest first, as the API returns them
        lt("2026-11-02T00:00:00Z", "c"),
        lt("2026-10-31T22:00:00Z", "b"),  # Nov in Hobart
        lt("2026-10-15T00:00:00Z", "a2"),
        lt("2026-10-01T00:00:00Z", "a1"),
    ]
    buckets = pl.bucket_by_month(tracks, HOBART)
    assert [t.uri for t in buckets[(2026, 10)]] == ["a1", "a2"]
    assert [t.uri for t in buckets[(2026, 11)]] == ["b", "c"]

    utc_buckets = pl.bucket_by_month(tracks, UTC)
    assert [t.uri for t in utc_buckets[(2026, 10)]] == ["a1", "a2", "b"]


def test_collect_since_stops_paging_at_cutoff():
    def gen():
        yield entry("2026-11-05T00:00:00Z", uri="spotify:track:new")
        yield {"added_at": "2026-11-04T00:00:00Z", "track": None}  # null item, skipped
        yield entry("2026-10-31T13:00:00Z", uri="spotify:track:edge")  # exactly at cutoff -> kept
        yield entry("2026-10-31T12:59:59Z", uri="spotify:track:old")  # before cutoff -> stop
        raise AssertionError("paginator was consumed past the cutoff")

    cutoff = pl.month_start_utc(2026, 11, HOBART)
    got = pl.collect_since(gen(), cutoff)
    assert [t.uri for t in got] == ["spotify:track:new", "spotify:track:edge"]


def test_collect_since_none_reads_all():
    entries = [entry("2020-01-01T00:00:00Z", uri="spotify:track:a"), entry("2010-01-01T00:00:00Z", uri="spotify:track:b")]
    assert len(pl.collect_since(entries, None)) == 2


# ----- playlists & dedupe ------------------------------------------------------

def test_find_owned_playlist_requires_exact_name_and_owner():
    pls = [
        {"id": "1", "name": "October 2026", "owner": {"id": "someone-else"}},
        {"id": "2", "name": "october 2026", "owner": {"id": "me"}},
        {"id": "3", "name": "October 2026 ", "owner": {"id": "me"}},
        None,
        {"id": "4", "name": "October 2026", "owner": {"id": "me"}},
    ]
    assert pl.find_owned_playlist(pls, "October 2026", "me")["id"] == "4"
    assert pl.find_owned_playlist(pls, "November 2026", "me") is None


def test_new_uris_keeps_order_and_dedupes():
    desired = ["a", "b", "c", "b", "d"]
    assert pl.new_uris(desired, ["c"]) == ["a", "b", "d"]
    assert pl.new_uris(desired, desired) == []
    assert pl.new_uris([], ["x"]) == []
