"""The identification use case: from a YouTube link to a saved Tracklist.

This is the one seam tests drive. Its boundaries (Set Source, Recogniser, model and Clock) are passed in.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic_ai import UsageLimitExceeded
from pydantic_ai.models import Model

from djset.agent import AgentDeps, identify_plays
from djset.audio import SetAudio
from djset.clock import Clock, SystemClock
from djset.comments import Comment, comments_for_agent, filter_comments
from djset.draft import Draft, Gap, build_draft
from djset.links import video_id
from djset.model_choice import load_model
from djset.progress import Progress, SilentProgress
from djset.recognition import Recogniser
from djset.render import markdown
from djset.scanner import Scanner
from djset.store import SetStore, default_root
from djset.tracklist import Evidence, Mode, Play, SetInfo, Tracklist

UNKNOWN_GAP = 30
"""A gap in recognition at least this long (in seconds) becomes an Unknown Track when the agent can't finish."""


class SetUnavailable(RuntimeError):
    """The video can't be used: private, removed, members-only, or blocked by a sign-in check."""


class MissingTool(RuntimeError):
    pass


class OutOfBudget(RuntimeError):
    pass


class SetSource(Protocol):
    """YouTube, or fixtures standing in for it."""

    def check(self, needs_audio: bool) -> None:
        """Raise MissingTool when a program the source needs isn't installed."""
        ...

    def set_info(self, video_id: str) -> SetInfo: ...

    def comments(self, video_id: str) -> list[Comment]: ...

    def audio(self, video_id: str, folder: Path) -> SetAudio:
        """The Set's audio, downloaded into `folder` unless a kept copy is already there."""
        ...


@dataclass
class Boundaries:
    set_source: SetSource
    recogniser: Recogniser
    model: Model | str
    clock: Clock = field(default_factory=SystemClock)
    progress: Progress = field(default_factory=SilentProgress)
    store_root: Path = field(default_factory=default_root)


@dataclass
class Request:
    link: str
    mode: Mode = Mode.NORMAL
    fresh: bool = False
    keep_audio: bool = False
    rerun: bool = False
    """Identify again even when a Tracklist is saved, reusing saved Recognitions (for evaluation)."""


@dataclass
class Outcome:
    tracklist: Tracklist
    markdown_path: Path
    reused: bool
    """True when a saved Tracklist was shown instead of running again."""


async def identify(request: Request, boundaries: Boundaries) -> Outcome:
    vid = video_id(request.link)
    store = SetStore(boundaries.store_root, vid)
    progress = boundaries.progress
    if request.fresh:
        store.clear()
    elif not request.rerun and (saved := store.load_tracklist(request.mode)):
        return Outcome(saved, store.tracklist_path(request.mode, ".md"), reused=True)

    model = load_model(boundaries.model)
    boundaries.set_source.check(needs_audio=request.mode != Mode.COMMENTS_ONLY)
    info = store.load_set_info()
    if info is None:
        progress.step("Fetching the Set's details")
        info = boundaries.set_source.set_info(vid)
        store.save_set_info(info)

    comments_text = None
    if request.mode != Mode.AUDIO_ONLY:
        comments = store.load_comments()
        if comments is None:
            progress.step("Fetching comments")
            comments = boundaries.set_source.comments(vid)
            store.save_comments(comments)
        comments_text = comments_for_agent(info.description, filter_comments(comments))

    scanner = None
    try:
        if request.mode != Mode.COMMENTS_ONLY:
            progress.step("Downloading the Set's audio")
            audio = boundaries.set_source.audio(vid, store.folder)
            scanner = Scanner(audio, boundaries.recogniser, store, boundaries.clock, progress, info.duration)
            progress.step("Recognising Clips")
            await scanner.scan_grid()
        progress.step(
            "Checking Recognitions against comments" if scanner and comments_text else "Writing the Tracklist"
        )
        deps = AgentDeps(set_info=info, mode=request.mode, comments=comments_text, scanner=scanner, progress=progress)
        try:
            plays = await identify_plays(deps, model)
        except UsageLimitExceeded as error:
            if scanner is None:
                raise OutOfBudget(f"The AI budget ran out before the Tracklist was finished ({error}).") from error
            progress.note("The AI budget ran out, so this Tracklist comes from recognition alone.")
            plays = plays_from_draft(build_draft(list(scanner.known.values())))
    finally:
        if not request.keep_audio:
            store.delete_audio()

    tracklist = Tracklist(set=info, mode=request.mode, plays=tidy(plays, request.mode, info.duration))
    return Outcome(tracklist, store.save_tracklist(tracklist, markdown(tracklist)), reused=False)


def tidy(plays: list[Play], mode: Mode, duration: int) -> list[Play]:
    """Put Plays in order, give each Blend one Timestamp, and match evidence to what the run could see."""
    forced = {Mode.AUDIO_ONLY: Evidence.AUDIO_ONLY, Mode.COMMENTS_ONLY: Evidence.COMMENTS_ONLY}.get(mode)

    def fixed(play: Play) -> Play:
        update: dict = {"timestamp": min(play.timestamp, duration)}
        if forced and play.track is not None:
            update["evidence"] = forced
        return play.model_copy(update=update)

    plays = [fixed(play) for play in plays]

    groups: dict[int, list[int]] = {}
    for index, play in enumerate(plays):
        if play.blend is not None:
            groups.setdefault(play.blend, []).append(index)
    for members in groups.values():
        if len(members) == 1:
            plays[members[0]] = plays[members[0]].model_copy(update={"blend": None})
        else:
            start = min(plays[i].timestamp for i in members)
            for i in members:
                plays[i] = plays[i].model_copy(update={"timestamp": start})

    plays.sort(key=lambda play: (play.timestamp, play.blend is None, play.blend or 0))
    numbers: dict[int, int] = {}
    for index, play in enumerate(plays):
        if play.blend is not None:
            number = numbers.setdefault(play.blend, len(numbers) + 1)
            plays[index] = play.model_copy(update={"blend": number})
    return plays


def plays_from_draft(draft: Draft) -> list[Play]:
    """A Tracklist from recognition alone: Plays heard by more than one Clip, and long gaps as Unknown Tracks."""
    plays = []
    for entry in draft.entries:
        if isinstance(entry, Gap):
            if entry.end - entry.start >= UNKNOWN_GAP:
                plays.append(Play(timestamp=entry.start, track=None, evidence=None))
        elif not entry.lone:
            plays.append(Play(timestamp=entry.timestamp, track=entry.track, evidence=Evidence.AUDIO_ONLY))
    return plays
