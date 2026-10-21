"""Stand-ins for the identification use case's boundaries: Set Source, Recogniser, model and Clock."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from djset.comments import Comment
from djset.recognition import Clip, Recognition, Refused
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
    """A model that plays a fixed script of turns, recording the tools offered and the tool results it was given."""

    def __init__(self, *turns: Turn):
        self.turns = list(turns)
        self.offered: list[str] = []
        self.results: dict[str, list[str]] = {}
        super().__init__(self._respond)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.offered = [tool.name for tool in info.function_tools]
        self.results = tool_results(messages)
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
    return lambda messages, info: [ToolCallPart(info.output_tools[0].name, {"plays": list(plays)})]


def play(
    timestamp: int, artist: str | None = None, title: str | None = None, evidence: str | None = None, **extra: Any
) -> dict[str, Any]:
    track_ = {"artist": artist, "title": title, **({"version": extra.pop("version")} if "version" in extra else {})}
    return {"timestamp": timestamp, "track": track_ if artist else None, "evidence": evidence, **extra}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())
