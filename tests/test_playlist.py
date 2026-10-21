"""Whole Playlists through the playlist use case, with a fake Spotify catalogue, a scripted Chooser and a fake Clock."""

import asyncio
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from djset.playlist import PlaylistBoundaries, PlaylistOutcome, make_playlist
from djset.render import print_proposal
from djset.spotify import SpotifyBusy, SpotifyUnavailable
from djset.tracklist import Evidence, Mode, Play, Tracklist
from tests.fakes import FakeClock, FakeSpotify, MadePlaylist, ScriptedChooser, read_json, set_info, spotify_track, track

WANNABE = spotify_track("Spice Girls", "Wannabe - Remastered 2011")
AGAIN = spotify_track("Fetty Wap", "Again")
NOKIA = spotify_track("Drake", "NOKIA")
U_REMIND_ME = spotify_track("Usher", "U Remind Me")
LOVE_ME_JEJE = spotify_track("Tems", "Love Me JeJe")
LOVE_ME_JEJE_FNKY = spotify_track("Tems, FNKY", "Love Me JeJe - FNKY Remix")
BE_YOUR_GIRL = spotify_track("Teedra Moses", "Be Your Girl")

CATALOGUE = [
    spotify_track("Karaoke Hits Band", "Wannabe (Originally Performed by Spice Girls)"),
    spotify_track("Spice Girls", "Wannabe - Instrumental"),
    spotify_track("Spice Girls", "Wannabe (Sped Up)"),
    WANNABE,
    AGAIN,
    NOKIA,
    spotify_track("Usher", "U Remind Me - Live"),
    U_REMIND_ME,
    LOVE_ME_JEJE,
    LOVE_ME_JEJE_FNKY,
    BE_YOUR_GIRL,
]


def heard(timestamp: int, artist: str, title: str, version: str | None = None, blend: int | None = None) -> Play:
    return Play(timestamp=timestamp, track=track(artist, title, version), evidence=Evidence.AUDIO_ONLY, blend=blend)


def unknown(timestamp: int) -> Play:
    return Play(timestamp=timestamp, track=None, evidence=None)


def tracklist(*plays: Play) -> Tracklist:
    return Tracklist(set=set_info(), mode=Mode.NORMAL, plays=list(plays))


def make(
    tracklist: Tracklist, tmp_path: Path, spotify: FakeSpotify, chooser=None, clock=None, progress=None
) -> PlaylistOutcome:
    extra = {"progress": progress} if progress else {}
    boundaries = PlaylistBoundaries(
        spotify=spotify, chooser=chooser or ScriptedChooser(), clock=clock or FakeClock(), store_root=tmp_path, **extra
    )
    return asyncio.run(make_playlist(tracklist, boundaries))


def names(outcome: PlaylistOutcome) -> list[str]:
    return [str(suggestion.match) for suggestion in outcome.proposal.suggestions]


class RecordingProgress:
    def __init__(self):
        self.pauses: list[tuple[float, str]] = []

    def step(self, message):
        pass

    def clips(self, done, total):
        pass

    def paused(self, wait, refusing_for, reason):
        self.pauses.append((wait, reason))

    def note(self, message):
        pass


def test_a_playlist_holds_the_spotify_match_of_every_chosen_play_in_tracklist_order(tmp_path):
    spotify = FakeSpotify(CATALOGUE)
    plays = tracklist(
        heard(15, "Spice Girls", "Wannabe"),
        heard(75, "Fetty Wap", "Again"),
        unknown(150),
        heard(210, "Usher", "U Remind Me"),
    )

    outcome = make(plays, tmp_path, spotify)

    assert spotify.playlists == {
        "playlist1": MadePlaylist(
            name="Test Set 001",
            description="The Tracks from the Set at https://www.youtube.com/watch?v=abcdefghijk",
            uris=[WANNABE.uri, AGAIN.uri, U_REMIND_ME.uri],
        )
    }
    assert outcome.playlist.url == "https://open.spotify.com/playlist/playlist1"
    saved = read_json(tmp_path / "abcdefghijk" / "playlist.json")
    assert [t["name"] for t in saved["tracks"]] == ["Wannabe - Remastered 2011", "Again", "U Remind Me"]
    assert saved["added"] == 3


def test_covers_and_other_versions_are_never_the_spotify_match_of_an_original(tmp_path):
    catalogue = [*CATALOGUE[:3], spotify_track("Usher", "U Remind Me - Live"), spotify_track("Usher", "Yeah!")]
    plays = tracklist(heard(15, "Spice Girls", "Wannabe"), heard(210, "Usher", "U Remind Me"))

    outcome = make(plays, tmp_path, FakeSpotify(catalogue))

    assert names(outcome) == []
    assert [str(play.track) for play in outcome.proposal.unmatched] == ["Spice Girls – Wannabe", "Usher – U Remind Me"]
    assert outcome.playlist is None


@pytest.mark.parametrize(
    "artist, title, spotify_artists, spotify_name",
    [
        ("Kid Ink Feat. Chris Brown", "Show Me", "Kid Ink, Chris Brown", "Show Me (feat. Chris Brown)"),
        ("Lil Pump & Kanye West", "I Love It", "Kanye West, Lil Pump", "I Love It (& Lil Pump)"),
        (
            "Chris Brown feat. Lil Wayne & Tyga",
            "Loyal",
            "Chris Brown, Lil Wayne, Tyga",
            "Loyal (feat. Lil Wayne & Tyga)",
        ),
        ("Snoop Dogg, T.I. feat. Jeremih", "Moon Juice", "Snoop Dogg, T.I., Jeremih", "Moon Juice"),
        ("The Black Eyed Peas", "Rock That Body", "Black Eyed Peas", "Rock That Body"),
        ("Beyonce", "Baby Boy", "Beyoncé, Sean Paul", "Baby Boy (feat. Sean Paul)"),
        ("The Weeknd", "Can’t Feel My Face", "The Weeknd", "Can't Feel My Face"),
        ("Kendrick Lamar feat. Rihanna", "LOYALTY", "Kendrick Lamar, Rihanna", "LOYALTY. FEAT. RIHANNA."),
        ("Alicia Keys", "No One", "Alicia Keys", "No One - Single Version"),
        ("Simon & Garfunkel", "Mrs. Robinson", "Simon & Garfunkel", 'Mrs. Robinson - From "The Graduate"'),
        ("Peggy Gou", "It Goes Like Nanana", "Peggy Gou", "(It Goes Like) Nanana"),
        ("MOLIY, Silent Addy", "Shake It To The Max (FLY)", "MOLIY, Silent Addy", "Shake It To The Max (FLY)"),
    ],
)
def test_release_labels_featured_artists_accents_and_punctuation_dont_stop_a_match(
    tmp_path, artist, title, spotify_artists, spotify_name
):
    expected = spotify_track(spotify_artists, spotify_name)
    catalogue = [spotify_track("Karaoke Kings", f"{title} (Karaoke Version)"), expected]

    outcome = make(tracklist(heard(15, artist, title)), tmp_path, FakeSpotify(catalogue))

    assert [(s.match.uri, s.match.original) for s in outcome.proposal.suggestions] == [(expected.uri, False)]


@pytest.mark.parametrize(
    "title, version",
    [("Love Me JeJe", "FNKY Remix"), ("Love Me JeJe (FNKY Remix)", None), ("Love Me JeJe - Fnky Remix", None)],
)
def test_an_edit_or_remix_on_spotify_is_matched_exactly(tmp_path, title, version):
    spotify = FakeSpotify(CATALOGUE)

    outcome = make(tracklist(heard(914, "Tems", title, version)), tmp_path, spotify)

    assert names(outcome) == ["Tems, FNKY – Love Me JeJe - FNKY Remix"]
    assert not outcome.playlist.tracks[0].original
    assert len(spotify.searches) == 1


def test_an_edit_not_on_spotify_becomes_its_original_marked_as_such(tmp_path):
    spotify = FakeSpotify(CATALOGUE)
    plays = tracklist(heard(844, "Teedra Moses", "Be Your Girl", "NAJ Soulful Reggae Cut"))

    outcome = make(plays, tmp_path, spotify)

    assert names(outcome) == ["Teedra Moses – Be Your Girl"]
    assert outcome.playlist.tracks[0].original
    assert spotify.searches == [
        "Be Your Girl NAJ Soulful Reggae Cut Teedra Moses",
        'track:"Be Your Girl" artist:"Teedra Moses"',
        "Teedra Moses Be Your Girl",
    ]
    screen = StringIO()
    print_proposal(Console(file=screen, width=160), outcome.proposal)
    assert "Teedra Moses – Be Your Girl (NAJ Soulful Reggae Cut)" in screen.getvalue()
    assert "Teedra Moses – Be Your Girl (the Original: this version isn't on Spotify)" in screen.getvalue()


def test_an_original_is_found_with_one_search(tmp_path):
    spotify = FakeSpotify(CATALOGUE)

    make(tracklist(heard(15, "Spice Girls", "Wannabe")), tmp_path, spotify)

    assert spotify.searches == ['track:"Wannabe" artist:"Spice Girls"']


def test_a_track_played_twice_is_added_once_and_each_track_of_a_blend_is_added(tmp_path):
    spotify = FakeSpotify(CATALOGUE)
    plays = tracklist(
        heard(15, "Spice Girls", "Wannabe"),
        heard(75, "Fetty Wap", "Again", blend=1),
        heard(75, "Drake", "NOKIA", blend=1),
        heard(240, "Spice Girls", "Wannabe"),
    )

    outcome = make(plays, tmp_path, spotify)

    suggestions = [(s.number, str(s.match), [p.timestamp for p in s.plays]) for s in outcome.proposal.suggestions]
    assert suggestions == [
        (1, "Spice Girls – Wannabe - Remastered 2011", [15, 240]),
        (2, "Fetty Wap – Again", [75]),
        (3, "Drake – NOKIA", [75]),
    ]
    assert spotify.playlists["playlist1"].uris == [WANNABE.uri, AGAIN.uri, NOKIA.uri]
    assert len(spotify.searches) == 3


def test_plays_not_on_spotify_are_listed_and_unknown_tracks_left_out(tmp_path):
    spotify = FakeSpotify(CATALOGUE)
    plays = tracklist(heard(15, "Spice Girls", "Wannabe"), unknown(100), heard(150, "Siangyoo", "Unreleased ID"))

    outcome = make(plays, tmp_path, spotify)

    assert [(p.timestamp, str(p.track)) for p in outcome.proposal.unmatched] == [(150, "Siangyoo – Unreleased ID")]
    assert names(outcome) == ["Spice Girls – Wannabe - Remastered 2011"]
    screen = StringIO()
    print_proposal(Console(file=screen, width=160), outcome.proposal)
    assert "Siangyoo – Unreleased ID" in screen.getvalue()
    assert "not on Spotify" in screen.getvalue()
    assert "1 Track found on Spotify, 1 not on Spotify" in screen.getvalue()
    assert "Unknown Track" not in screen.getvalue()


def test_the_listener_can_leave_plays_out(tmp_path):
    spotify = FakeSpotify(CATALOGUE)
    plays = tracklist(
        heard(15, "Spice Girls", "Wannabe"), heard(75, "Fetty Wap", "Again"), heard(160, "Drake", "NOKIA")
    )

    make(plays, tmp_path, spotify, chooser=ScriptedChooser(leave_out=(2,)))

    assert spotify.playlists["playlist1"].uris == [WANNABE.uri, NOKIA.uri]


def test_cancelling_makes_nothing_and_the_next_run_asks_again_without_searching_again(tmp_path):
    plays = tracklist(heard(15, "Spice Girls", "Wannabe"), heard(75, "Fetty Wap", "Again"))
    first = FakeSpotify(CATALOGUE)

    cancelled = make(plays, tmp_path, first, chooser=ScriptedChooser(cancel=True))

    assert cancelled.playlist is None
    assert first.playlists == {}
    assert not (tmp_path / "abcdefghijk" / "playlist.json").exists()
    second = FakeSpotify(CATALOGUE)
    chooser = ScriptedChooser()
    made = make(plays, tmp_path, second, chooser=chooser)
    assert second.searches == []
    assert len(chooser.proposals) == 1
    assert second.playlists["playlist1"].uris == [WANNABE.uri, AGAIN.uri]
    assert not made.reused


def test_a_made_playlist_is_shown_again_instead_of_made_twice(tmp_path):
    plays = tracklist(heard(15, "Spice Girls", "Wannabe"))
    make(plays, tmp_path, FakeSpotify(CATALOGUE))
    spotify = FakeSpotify(CATALOGUE)
    chooser = ScriptedChooser()

    again = make(plays, tmp_path, spotify, chooser=chooser)

    assert again.reused
    assert again.playlist.url == "https://open.spotify.com/playlist/playlist1"
    assert (spotify.searches, spotify.playlists, chooser.proposals) == ([], {}, [])


def test_a_playlist_stopped_part_way_is_finished_by_the_next_run(tmp_path):
    catalogue = [spotify_track(f"Artist {i}", f"Track {i}") for i in range(130)]
    plays = tracklist(*(heard(i * 30, f"Artist {i}", f"Track {i}") for i in range(130)))
    spotify = FakeSpotify(catalogue, fail_on_add=2)

    with pytest.raises(SpotifyUnavailable):
        make(plays, tmp_path, spotify)

    assert [len(uris) for uris in spotify.add_requests] == [100]
    spotify.fail_on_add = None
    chooser = ScriptedChooser()
    again = make(plays, tmp_path, spotify, chooser=chooser)
    assert again.reused
    assert chooser.proposals == []
    assert list(spotify.playlists) == ["playlist1"]
    assert spotify.playlists["playlist1"].uris == [t.uri for t in catalogue]
    assert [len(uris) for uris in spotify.add_requests] == [100, 30]


def test_a_busy_spotify_is_waited_for_as_long_as_it_asks(tmp_path):
    clock = FakeClock()
    progress = RecordingProgress()
    busy = [
        SpotifyBusy("Spotify answered HTTP 429 (API rate limit exceeded)", retry_after=7),
        SpotifyBusy("Spotify couldn't be reached (ConnectError)"),
        SpotifyBusy("Spotify answered HTTP 503"),
    ]
    spotify = FakeSpotify(CATALOGUE, busy=busy)

    outcome = make(tracklist(heard(15, "Spice Girls", "Wannabe")), tmp_path, spotify, clock=clock, progress=progress)

    assert clock.sleeps == [7, 5, 10]
    assert progress.pauses[0] == (7, "Spotify answered HTTP 429 (API rate limit exceeded)")
    assert spotify.playlists["playlist1"].uris == [WANNABE.uri]
    assert outcome.playlist.added == 1


def test_a_spotify_busy_for_too_long_stops_and_the_next_run_carries_on(tmp_path):
    clock = FakeClock()
    plays = tracklist(heard(15, "Spice Girls", "Wannabe"), heard(75, "Fetty Wap", "Again"))

    with pytest.raises(SpotifyUnavailable, match="run the same command later"):
        make(plays, tmp_path, FakeSpotify(CATALOGUE, busy_after=1), clock=clock)

    assert 240 <= sum(clock.sleeps) <= 300
    healthy = FakeSpotify(CATALOGUE)
    make(plays, tmp_path, healthy)
    assert healthy.searches == ['track:"Again" artist:"Fetty Wap"']
    assert healthy.playlists["playlist1"].uris == [WANNABE.uri, AGAIN.uri]


def test_a_used_up_quota_stops_at_once(tmp_path):
    clock = FakeClock()
    quota = SpotifyBusy("Spotify answered HTTP 429 (Too many requests QUOTA_EXCEEDED)", retry_after=86400)

    with pytest.raises(SpotifyUnavailable, match="QUOTA_EXCEEDED"):
        make(
            tracklist(heard(15, "Spice Girls", "Wannabe")), tmp_path, FakeSpotify(CATALOGUE, busy=[quota]), clock=clock
        )

    assert clock.sleeps == []
