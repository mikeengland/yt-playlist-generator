"""The playlist use case: from a Tracklist to a private Spotify playlist of the Plays the listener chooses.

This is the second seam tests drive. Its boundaries (Spotify, the Chooser, the Clock and Progress) are passed in.
No AI model is involved, and no Spotify data may ever reach one: Spotify's Developer Policy forbids it.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from djset.clock import Clock, SystemClock
from djset.matching import searches, spotify_match
from djset.progress import Progress, SilentProgress
from djset.spotify import (
    Playlist,
    Search,
    Spotify,
    SpotifyBusy,
    SpotifyMatch,
    SpotifyPlaylist,
    SpotifyTrack,
    SpotifyUnavailable,
)
from djset.store import SetStore, default_root
from djset.tracklist import Play, SetInfo, Track, Tracklist

ADD_AT_ONCE = 100
"""Spotify adds at most 100 items per request."""
FIRST_WAIT = 5.0
LONGEST_WAIT = 60.0
GIVE_UP_AFTER = 300.0

T = TypeVar("T")


@dataclass
class Suggestion:
    """A Spotify Match proposed for the Playlist, with the Plays it stands for."""

    number: int
    match: SpotifyMatch
    plays: list[Play]


@dataclass
class Proposal:
    name: str
    suggestions: list[Suggestion]
    """One per distinct Spotify Match, in Tracklist order."""
    unmatched: list[Play]
    """One Play per Track that has no Spotify Match. Unknown Tracks are left out altogether."""


class Chooser(Protocol):
    def choose(self, proposal: Proposal) -> list[Suggestion]:
        """The suggestions to add to the Playlist; none to make no Playlist."""
        ...


@dataclass
class PlaylistBoundaries:
    spotify: Spotify
    chooser: Chooser
    clock: Clock = field(default_factory=SystemClock)
    progress: Progress = field(default_factory=SilentProgress)
    store_root: Path = field(default_factory=default_root)


@dataclass
class PlaylistOutcome:
    playlist: Playlist | None
    """None when the listener chose nothing."""
    proposal: Proposal | None
    """None when the Set's Playlist was made by an earlier run."""
    reused: bool


async def make_playlist(tracklist: Tracklist, boundaries: PlaylistBoundaries) -> PlaylistOutcome:
    store = SetStore(boundaries.store_root, tracklist.set.video_id)
    progress = boundaries.progress
    spotify = _Patient(boundaries.spotify, boundaries.clock, progress)
    if (made := store.load_playlist()) is not None:
        if made.added < len(made.tracks):
            progress.step("Adding the rest of the Tracks to the Spotify playlist")
            await _fill(made, spotify, store)
        return PlaylistOutcome(made, proposal=None, reused=True)

    found = store.spotify_searches()
    tracks = list({play.track.key(): play.track for play in tracklist.plays if play.track is not None}.values())
    to_search = [track for track in tracks if track.key() not in found]
    if to_search:
        progress.step(f"Finding {len(to_search)} Tracks on Spotify")
    for track in to_search:
        search = Search(track=track, match=await _find(track, spotify))
        store.add_spotify_search(search)
        found[track.key()] = search

    proposal = _proposal(tracklist, found)
    chosen = boundaries.chooser.choose(proposal)
    if not chosen:
        return PlaylistOutcome(None, proposal, reused=False)
    progress.step("Making the Spotify playlist")
    created = await spotify.create_playlist(proposal.name, _description(tracklist.set))
    playlist = Playlist(id=created.id, url=created.url, name=proposal.name, tracks=[s.match for s in chosen])
    store.save_playlist(playlist)
    await _fill(playlist, spotify, store)
    return PlaylistOutcome(playlist, proposal, reused=False)


async def _find(track: Track, spotify: "_Patient") -> SpotifyMatch | None:
    """The first exact Spotify Match the searches give, or else the first Original."""
    original = None
    for query in searches(track):
        match = spotify_match(track, await spotify.search(query))
        if match and not match.original:
            return match
        original = original or match
    return original


def _proposal(tracklist: Tracklist, found: dict[tuple[str, str, str], Search]) -> Proposal:
    suggestions: dict[str, Suggestion] = {}
    unmatched: dict[tuple[str, str, str], Play] = {}
    for play in tracklist.plays:
        if play.track is None:
            continue
        match = found[play.track.key()].match
        if match is None:
            unmatched.setdefault(play.track.key(), play)
        elif match.uri in suggestions:
            suggestions[match.uri].plays.append(play)
        else:
            suggestions[match.uri] = Suggestion(number=len(suggestions) + 1, match=match, plays=[play])
    return Proposal(tracklist.set.title, list(suggestions.values()), list(unmatched.values()))


def _description(info: SetInfo) -> str:
    return f"The Tracks from the Set at {info.url}"


async def _fill(playlist: Playlist, spotify: "_Patient", store: SetStore) -> None:
    """Add the Playlist's Tracks that aren't on Spotify yet, saving progress after each request."""
    while playlist.added < len(playlist.tracks):
        chunk = playlist.tracks[playlist.added : playlist.added + ADD_AT_ONCE]
        await spotify.add_items(playlist.id, [match.uri for match in chunk])
        playlist.added += len(chunk)
        store.save_playlist(playlist)


class _Patient:
    """Spotify, retried while it is busy: waiting as long as it asks, or from 5 seconds doubling to a minute.

    It gives up on a request once waiting would pass 5 minutes, or at once when Spotify asks for a longer wait.
    """

    def __init__(self, spotify: Spotify, clock: Clock, progress: Progress):
        self._spotify = spotify
        self._clock = clock
        self._progress = progress

    async def search(self, query: str) -> list[SpotifyTrack]:
        return await self._retried(lambda: self._spotify.search(query))

    async def create_playlist(self, name: str, description: str) -> SpotifyPlaylist:
        return await self._retried(lambda: self._spotify.create_playlist(name, description))

    async def add_items(self, playlist_id: str, uris: list[str]) -> None:
        await self._retried(lambda: self._spotify.add_items(playlist_id, uris))

    async def _retried(self, request: Callable[[], Awaitable[T]]) -> T:
        wait = FIRST_WAIT
        waited = 0.0
        while True:
            try:
                return await request()
            except SpotifyBusy as busy:
                pause = busy.retry_after if busy.retry_after is not None else wait
                if waited + pause > GIVE_UP_AFTER:
                    raise SpotifyUnavailable(
                        f"Spotify is busy ({busy}). Everything found so far is saved; "
                        "run the same command later to carry on."
                    ) from busy
                self._progress.paused(pause, waited, str(busy))
                await self._clock.sleep(pause)
                waited += pause
                if busy.retry_after is None:
                    wait = min(wait * 2, LONGEST_WAIT)
