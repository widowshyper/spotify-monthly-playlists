from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sync import PlaylistSyncer

HOBART = ZoneInfo("Australia/Hobart")
UTC = timezone.utc


class FakeSpotify:
    """In-memory stand-in for SpotifyClient."""

    def __init__(self, liked, playlists=None):
        self.liked = liked  # newest first, raw API entries
        self.playlists = playlists or []  # dicts with id/name/owner/uris
        self.saved_read = 0
        self.created = []

    def me(self):
        return {"id": "me", "display_name": "Me"}

    def iter_saved_tracks(self):
        for e in self.liked:
            self.saved_read += 1
            yield e

    def iter_my_playlists(self):
        return iter([{k: v for k, v in p.items() if k != "uris"} for p in self.playlists])

    def iter_playlist_items(self, pid):
        p = next(p for p in self.playlists if p["id"] == pid)
        return iter([{"is_local": False, "item": {"uri": u}} for u in p["uris"]])

    def create_playlist(self, name, public, description):
        p = {"id": f"pl{len(self.playlists)}", "name": name, "owner": {"id": "me"},
             "public": public, "description": description, "uris": []}
        self.playlists.append(p)
        self.created.append(p)
        return {k: v for k, v in p.items() if k != "uris"}

    def add_items(self, pid, uris):
        next(p for p in self.playlists if p["id"] == pid)["uris"].extend(uris)
        return len(uris)


def liked(added_at, n):
    return {"added_at": added_at, "track": {"uri": f"spotify:track:{n}", "name": n, "artists": [{"name": "A"}]}}


LIKED = [  # newest first
    liked("2026-11-03T00:00:00Z", "n2"),
    liked("2026-10-31T22:00:00Z", "n1"),   # 1 Nov 9am in Hobart
    liked("2026-10-20T00:00:00Z", "o2"),
    liked("2026-10-02T00:00:00Z", "o1"),
    liked("2026-08-15T00:00:00Z", "a1"),
]


def test_creates_playlist_ordered_oldest_first_and_private_by_default():
    fake = FakeSpotify(LIKED)
    r = PlaylistSyncer(fake, tz=HOBART).sync_one(2026, 10)
    assert r.action == "created" and r.added == 2
    p = fake.created[0]
    assert p["name"] == "October 2026"
    assert p["public"] is False
    assert p["description"] == "Liked songs from October 2026. Auto-generated."
    assert p["uris"] == ["spotify:track:o1", "spotify:track:o2"]


def test_rerun_never_duplicates_playlist_or_tracks():
    fake = FakeSpotify(LIKED)
    PlaylistSyncer(fake, tz=HOBART).sync_one(2026, 11)
    r = PlaylistSyncer(fake, tz=HOBART).sync_one(2026, 11)
    assert r.action == "unchanged"
    assert len(fake.playlists) == 1
    assert fake.playlists[0]["uris"] == ["spotify:track:n1", "spotify:track:n2"]


def test_existing_playlist_gets_only_missing_tracks_and_nothing_removed():
    fake = FakeSpotify(LIKED, playlists=[
        {"id": "x", "name": "October 2026", "owner": {"id": "me"}, "uris": ["spotify:track:o2", "spotify:track:manual"]},
    ])
    r = PlaylistSyncer(fake, tz=HOBART).sync_one(2026, 10)
    assert r.action == "updated" and r.added == 1
    assert fake.playlists[0]["uris"] == ["spotify:track:o2", "spotify:track:manual", "spotify:track:o1"]
    assert fake.created == []


def test_same_name_playlist_owned_by_someone_else_is_ignored():
    fake = FakeSpotify(LIKED, playlists=[{"id": "x", "name": "October 2026", "owner": {"id": "other"}, "uris": []}])
    r = PlaylistSyncer(fake, tz=HOBART).sync_one(2026, 10)
    assert r.action == "created"


def test_empty_month_is_skipped_not_created():
    fake = FakeSpotify(LIKED)
    r = PlaylistSyncer(fake, tz=HOBART).sync_one(2026, 9)
    assert r.action == "skipped"
    assert fake.created == []


def test_auto_syncs_previous_and_current_month_and_stops_paging_early():
    fake = FakeSpotify(LIKED)
    now = datetime(2026, 11, 10, tzinfo=UTC)
    results = PlaylistSyncer(fake, tz=HOBART).auto(now=now)
    assert [(r.name, r.action, r.added) for r in results] == [
        ("October 2026", "created", 2),
        ("November 2026", "created", 2),
    ]
    # Read up to and including the first August entry, then stopped.
    assert fake.saved_read == 5


def test_preview_lists_only_tracks_that_would_be_added():
    fake = FakeSpotify(LIKED, playlists=[
        {"id": "x", "name": "October 2026", "owner": {"id": "me"}, "uris": ["spotify:track:o1"]},
    ])
    pv = PlaylistSyncer(fake, tz=HOBART).preview(2026, 10)
    assert [t.uri for t in pv.tracks] == ["spotify:track:o1", "spotify:track:o2"]
    assert [t.uri for t in pv.to_add] == ["spotify:track:o2"]
    assert pv.existing_playlist["id"] == "x"


def test_backfill_from_month_only_includes_that_month_onwards_and_stops_paging():
    fake = FakeSpotify(LIKED)
    s = PlaylistSyncer(fake, tz=HOBART)
    plan = s.plan_backfill(since=(2026, 10))
    assert plan.months == [(2026, 10), (2026, 11)]  # August excluded
    assert fake.saved_read == 5  # read up to the first August entry, then stopped
    results = s.run_backfill(plan)
    assert [r.name for r in results] == ["October 2026", "November 2026"]
    assert [p["name"] for p in fake.created] == ["October 2026", "November 2026"]


def test_backfill_from_month_respects_local_timezone_boundary():
    # 22:00 UTC on 31 Oct is 1 Nov in Hobart, so it belongs to a November backfill
    fake = FakeSpotify(LIKED)
    plan = PlaylistSyncer(fake, tz=HOBART).plan_backfill(since=(2026, 11))
    assert plan.months == [(2026, 11)]
    assert [t.uri for t in plan.buckets[(2026, 11)]] == ["spotify:track:n1", "spotify:track:n2"]


def test_backfill_from_month_with_no_songs_is_empty():
    plan = PlaylistSyncer(FakeSpotify(LIKED), tz=HOBART).plan_backfill(since=(2026, 12))
    assert plan.months == []


def test_result_descriptions_use_correct_plurals():
    from sync import SyncResult
    assert SyncResult(2026, 8, "August 2026", "created", 1).describe() == "Created 'August 2026' with 1 track."
    assert SyncResult(2026, 8, "August 2026", "created", 2).describe() == "Created 'August 2026' with 2 tracks."
    assert SyncResult(2026, 8, "August 2026", "updated", 1).describe() == "Updated 'August 2026' with 1 new track."


def test_backfill_plan_counts_and_run():
    fake = FakeSpotify(LIKED, playlists=[{"id": "x", "name": "August 2026", "owner": {"id": "me"}, "uris": []}])
    s = PlaylistSyncer(fake, tz=HOBART)
    plan = s.plan_backfill()
    assert plan.months == [(2026, 8), (2026, 10), (2026, 11)]
    assert plan.to_create == [(2026, 10), (2026, 11)]
    assert plan.to_update == [(2026, 8)]
    seen = []
    results = s.run_backfill(plan, progress=lambda i, n: seen.append((i, n)))
    assert [r.action for r in results] == ["updated", "created", "created"]
    assert seen == [(1, 3), (2, 3), (3, 3)]
