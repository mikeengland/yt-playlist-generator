"""Stand-ins for the use cases' boundaries: Set Source, Recogniser, model and Clock, and Spotify and the Chooser."""

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from djset.comments import Comment
from djset.playlist import Correction, Proposal, Suggestion
from djset.recognition import Clip, Recognition, Refused
from djset.spotify import SpotifyBusy, SpotifyPlaylist, SpotifyTrack, SpotifyUnavailable
from djset.tracklist import SetInfo, Track

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClock:
    def __init__(self) -> None:
        self.time = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.time

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += seconds


class FakeAudio:
    def cut(self, clip: Clip, wav: Path) -> None:
        wav.write_bytes(b"")


@dataclass
class FixtureSource:
    """A Set Source backed by fixtures of real Set info and comments, or by values given in the test."""

    info: SetInfo
    comments_: list[Comment] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    @classmethod
    def from_fixture(cls, video_id: str) -> "FixtureSource":
        folder = FIXTURES / video_id
        info = SetInfo.model_validate_json((folder / "set.json").read_text())
        comments = TypeAdapter(list[Comment]).validate_json((folder / "comments.json").read_text())
        return cls(info, comments)

    def check(self, needs_audio: bool) -> None:
        pass

    def set_info(self, video_id: str) -> SetInfo:
        self.calls.append("set_info")
        return self.info

    def comments(self, video_id: str) -> list[Comment]:
        self.calls.append("comments")
        return self.comments_

    def audio(self, video_id: str, folder: Path) -> FakeAudio:
        self.calls.append("audio")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "audio.m4a").write_bytes(b"not really audio")
        return FakeAudio()


def set_info(duration: int = 300, video_id: str = "abcdefghijk", description: str = "") -> SetInfo:
    return SetInfo(
        video_id=video_id, title="Test Set 001", channel="Test DJ", duration=duration, description=description
    )


@dataclass
class Segment:
    start: int
    end: int
    track: Track | None


class TimelineRecogniser:
    """Hears whatever the timeline says plays at a Clip's middle. Can refuse during stretches of clock time."""

    def __init__(
        self,
        segments: list[Segment],
        clock: FakeClock | None = None,
        refuse_between: list[tuple[float, float]] = (),
        refuse_after: int | None = None,
    ):
        self.segments = segments
        self.clock = clock
        self.refuse_between = list(refuse_between)
        self.refuse_after = refuse_after
        self.requests: list[Clip] = []
        self.refusals = 0

    async def recognise(self, clip: Clip, wav: Path) -> Track | None:
        refusing = self.refuse_after is not None and len(self.requests) >= self.refuse_after
        if self.clock and any(start <= self.clock.now() < end for start, end in self.refuse_between):
            refusing = True
        if refusing:
            self.refusals += 1
            raise Refused("Shazam answered HTTP 429")
        self.requests.append(clip)
        middle = clip.start + clip.length / 2
        return next((s.track for s in self.segments if s.start <= middle < s.end), None)


class ReplayRecogniser:
    """Replays Recognitions saved by a real run (recognitions.jsonl), looked up by Clip offset and length."""

    def __init__(self, path: Path):
        lines = path.read_text().splitlines()
        self.saved = {r.clip: r.track for r in (Recognition.model_validate_json(line) for line in lines if line)}

    async def recognise(self, clip: Clip, wav: Path) -> Track | None:
        return self.saved.get(clip)


def track(artist: str, title: str, version: str | None = None) -> Track:
    return Track(artist=artist, title=title, version=version)


Turn = Callable[[list[ModelMessage], AgentInfo], list[Any]]


class ScriptedModel(FunctionModel):
    """A model that plays a fixed script of turns, recording the tools offered, the prompts and tool results it was
    given, and what it was asked to retry."""

    def __init__(self, *turns: Turn):
        self.turns = list(turns)
        self.offered: list[str] = []
        self.prompts: list[str] = []
        self.results: dict[str, list[str]] = {}
        self.retries: list[str] = []
        super().__init__(self._respond)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.offered = [tool.name for tool in info.function_tools]
        self.results = tool_results(messages)
        parts = [part for message in messages if isinstance(message, ModelRequest) for part in message.parts]
        self.prompts = [str(part.content) for part in parts if isinstance(part, UserPromptPart)]
        self.retries = [part.model_response() for part in parts if isinstance(part, RetryPromptPart)]
        turn = self.turns.pop(0)
        return ModelResponse(parts=turn(messages, info))


def tool_results(messages: list[ModelMessage]) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    results.setdefault(part.tool_name, []).append(str(part.content))
    return results


def call(*calls: tuple[str, dict[str, Any]]) -> Turn:
    """A turn that calls tools: call(("draft_tracklist", {}), ("comments", {}))."""
    return lambda messages, info: [ToolCallPart(name, args) for name, args in calls]


def finish(*plays: dict[str, Any]) -> Turn:
    """A turn that returns the final Tracklist."""
    return output(plays=list(plays))


def output(**fields: Any) -> Turn:
    """A turn that returns the agent's output."""
    return lambda messages, info: [ToolCallPart(info.output_tools[0].name, fields)]


def play(
    timestamp: int, artist: str | None = None, title: str | None = None, evidence: str | None = None, **extra: Any
) -> dict[str, Any]:
    track_ = {"artist": artist, "title": title, **({"version": extra.pop("version")} if "version" in extra else {})}
    return {"timestamp": timestamp, "track": track_ if artist else None, "evidence": evidence, **extra}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def spotify_track(artists: str, name: str) -> SpotifyTrack:
    """A Spotify track named as Spotify names it, e.g. spotify_track("Tems, FNKY", "Love Me JeJe - FNKY Remix")."""
    id_ = re.sub(r"\W+", "", f"{artists}{name}")[:22]
    return SpotifyTrack(
        uri=f"spotify:track:{id_}", url=f"https://open.spotify.com/track/{id_}", name=name, artists=artists.split(", ")
    )


@dataclass
class MadePlaylist:
    name: str
    description: str
    uris: list[str] = field(default_factory=list)


class FakeSpotify:
    """Answers searches from a small catalogue: tracks whose name and artists hold every word searched for (ignoring
    case and accents), in catalogue order, at most 10. Records searches and the playlists made. It can be busy for
    the next few requests or for every request after the first few, and can fail adding items."""

    def __init__(
        self,
        catalogue: list[SpotifyTrack],
        busy: list[SpotifyBusy] = (),
        busy_after: int | None = None,
        fail_on_add: int | None = None,
    ) -> None:
        self.catalogue = catalogue
        self.busy = list(busy)
        self.busy_after = busy_after
        self.fail_on_add = fail_on_add
        self.requests = 0
        self.searches: list[str] = []
        self.playlists: dict[str, MadePlaylist] = {}
        self.add_requests: list[list[str]] = []

    async def search(self, query: str) -> list[SpotifyTrack]:
        self._maybe_busy()
        self.searches.append(query)
        wanted = _words(re.sub(r"\b(?:track|artist):", " ", query))
        return [t for t in self.catalogue if wanted <= _words(f"{t.name} {' '.join(t.artists)}")][:10]

    async def create_playlist(self, name: str, description: str) -> SpotifyPlaylist:
        self._maybe_busy()
        id_ = f"playlist{len(self.playlists) + 1}"
        self.playlists[id_] = MadePlaylist(name, description)
        return SpotifyPlaylist(id=id_, url=f"https://open.spotify.com/playlist/{id_}")

    async def add_items(self, playlist_id: str, uris: list[str]) -> None:
        self._maybe_busy()
        if self.fail_on_add is not None and len(self.add_requests) + 1 == self.fail_on_add:
            raise SpotifyUnavailable("Spotify refused the request (HTTP 400: Invalid base62 id).")
        assert len(uris) <= 100, "Spotify adds at most 100 items per request"
        self.add_requests.append(uris)
        self.playlists[playlist_id].uris.extend(uris)

    def _maybe_busy(self) -> None:
        if self.busy:
            raise self.busy.pop(0)
        if self.busy_after is not None and self.requests >= self.busy_after:
            raise SpotifyBusy("Spotify answered HTTP 503")
        self.requests += 1


def _words(text: str) -> set[str]:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return set(re.findall(r"\w+", plain.casefold()))


class ScriptedChooser:
    """Answers with each of `corrections` in turn, then chooses every suggestion except those numbered in `leave_out`,
    or none when cancelling. Records proposals."""

    def __init__(
        self, leave_out: tuple[int, ...] = (), cancel: bool = False, corrections: tuple[str, ...] = ()
    ) -> None:
        self.leave_out = leave_out
        self.cancel = cancel
        self.corrections = list(corrections)
        self.proposals: list[Proposal] = []

    def choose(self, proposal: Proposal) -> list[Suggestion] | Correction:
        self.proposals.append(proposal)
        if self.corrections:
            return Correction(self.corrections.pop(0))
        if self.cancel:
            return []
        return [s for s in proposal.suggestions if s.number not in self.leave_out]
