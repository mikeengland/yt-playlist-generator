"""The correction use case: the listener's Correction, in their own words, applied to a Tracklist.

This is the third seam tests drive. Its boundaries (the model and Progress) are passed in. The model only proposes
changes to the numbered Plays it is shown, and plain code applies them, so Plays the Correction doesn't mention stay as
they were. The model is shown the Tracklist and the Correction, never anything from Spotify.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import (
    Agent,
    ModelAPIError,
    ModelRetry,
    RunContext,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.models import Model

from djset.agent import TERMS
from djset.links import clock
from djset.model_choice import load_model
from djset.progress import Progress, SilentProgress
from djset.render import markdown, track_text
from djset.store import SetStore, default_root
from djset.tracklist import Evidence, Play, Track, Tracklist, in_order

USAGE_LIMITS = UsageLimits(request_limit=5)

ROLE = "You correct the Tracklist of one recorded DJ Set from YouTube, as the listener tells you.\n\n"

RULES = """\
- Correction: the listener's change to the Tracklist, in their own words.

Return the changes that make the Tracklist say what the listener said, referring to Plays by their number in the list:
- name: the Track a Play really is, or null to make it an Unknown Track. A partial Correction ("2:40 is by Drake") \
keeps the rest of the Track as it was.
- add: a Play the Tracklist is missing. Give blend_with when it is heard over a listed Play.
- remove: a Play that isn't in the Set at all.
- move: a Play's new Timestamp.

Rules:
- The listener's word is the evidence: it outranks Recognitions and Comment Hints. Name only Tracks the listener \
named. Write names as they are usually written (capitalisation, obvious typos), but never swap in a different Track.
- The listener refers to Plays by Timestamp (m:ss or h:mm:ss) or by the Track shown. A Play within about 30 seconds of \
the Timestamp they give is the one they mean.
- Timestamps you return are whole seconds into the Set.
- Remove a Play only when the listener says it isn't in the Set. Leaving a Track out of the Spotify playlist isn't a \
Correction: the listener does that by number at the playlist prompt.
- When you can't tell what to change, leave it out and say why in reply, in one short sentence.
"""


class NameTrack(BaseModel):
    """Name the Track a Play really is."""

    change: Literal["name"]
    play: int = Field(description="The Play's number in the list.")
    track: Track | None = Field(description="The Track, or null to make the Play an Unknown Track.")


class AddPlay(BaseModel):
    """Add a Play the Tracklist is missing."""

    change: Literal["add"]
    timestamp: int = Field(ge=0, description="Seconds into the Set where the Play is first heard.")
    track: Track | None = Field(description="The Track, or null for an Unknown Track.")
    blend_with: int | None = Field(
        default=None, description="The number of a listed Play heard at the same time, if it is a Blend."
    )


class RemovePlay(BaseModel):
    """Remove a Play that isn't in the Set."""

    change: Literal["remove"]
    play: int = Field(description="The Play's number in the list.")


class MovePlay(BaseModel):
    """Move a Play, and the rest of its Blend, to a new Timestamp."""

    change: Literal["move"]
    play: int = Field(description="The Play's number in the list.")
    timestamp: int = Field(ge=0, description="Seconds into the Set where the Play is first heard.")


ProposedChange = Annotated[NameTrack | AddPlay | RemovePlay | MovePlay, Field(discriminator="change")]


class Proposed(BaseModel):
    changes: list[ProposedChange]
    reply: str | None = Field(
        default=None,
        description="One short sentence for the listener on anything you couldn't change, and why; null otherwise.",
    )


class CorrectionFailed(RuntimeError):
    """The model failed, or kept proposing changes that can't be made. The Tracklist is unchanged."""


@dataclass
class Change:
    """One change made to the Tracklist."""

    before: Play | None
    """None for an added Play."""
    after: Play | None
    """None for a removed Play."""


@dataclass
class CorrectionBoundaries:
    model: Model | str
    progress: Progress = field(default_factory=SilentProgress)
    store_root: Path = field(default_factory=default_root)


@dataclass
class Corrected:
    tracklist: Tracklist
    changes: list[Change]
    reply: str | None
    """What the model said it couldn't change, and why."""


agent = Agent(
    deps_type=Tracklist,
    output_type=Proposed,
    instructions=ROLE + TERMS + RULES,
    retries=3,
    defer_model_check=True,
)


@agent.output_validator
def _possible(ctx: RunContext[Tracklist], proposed: Proposed) -> Proposed:
    _apply(ctx.deps, proposed.changes)
    return proposed


async def correct(tracklist: Tracklist, correction: str, boundaries: CorrectionBoundaries) -> Corrected:
    """The Tracklist with the listener's Correction made, saved when anything changed."""
    model = load_model(boundaries.model)
    boundaries.progress.step("Correcting the Tracklist")
    try:
        result = await agent.run(
            f"{numbered(tracklist)}\n\nThe listener's Correction: {correction}",
            model=model,
            deps=tracklist,
            usage_limits=USAGE_LIMITS,
        )
    except (ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded) as error:
        raise CorrectionFailed(
            f"The AI model couldn't make the Correction ({error}). The Tracklist is unchanged."
        ) from error
    plays, changes = _apply(tracklist, result.output.changes)
    if changes:
        tracklist = tracklist.model_copy(update={"plays": plays})
        SetStore(boundaries.store_root, tracklist.set.video_id).save_tracklist(tracklist, markdown(tracklist))
    return Corrected(tracklist, changes, result.output.reply)


def numbered(tracklist: Tracklist) -> str:
    """The Tracklist as the model sees it: numbered Plays with their Timestamps, Tracks, evidence, Blends and notes."""
    info = tracklist.set
    lines = [f"Set: {info.title} ({clock(info.duration)}, {info.duration} seconds long)", "Tracklist:"]
    for number, play in enumerate(tracklist.plays, 1):
        details = [play.evidence.value] if play.evidence else []
        if play.blend is not None:
            details.append(f"Blend {play.blend}")
        line = f"{number}. {clock(play.timestamp)} ({play.timestamp}s)  {track_text(play)}"
        lines.append(f"{line}  [{'; '.join(details + play.notes)}]" if details or play.notes else line)
    return "\n".join(lines)


def _apply(tracklist: Tracklist, proposed: list[ProposedChange]) -> tuple[list[Play], list[Change]]:
    """The Plays after the changes, in order, and the changes made. Raises ModelRetry for a change that can't be made.

    Changes refer to Plays by their number in the Tracklist before any change.
    """
    plays: list[Play | None] = list(tracklist.plays)
    made: list[Change] = []

    def listed(number: int) -> int:
        if not 1 <= number <= len(tracklist.plays):
            raise ModelRetry(f"There is no Play {number}: the Plays are numbered 1 to {len(tracklist.plays)}.")
        if plays[number - 1] is None:
            raise ModelRetry(f"Play {number} is removed, so it can't also be changed.")
        return number - 1

    def within_set(timestamp: int) -> None:
        if timestamp > tracklist.set.duration:
            raise ModelRetry(f"{timestamp} is past the end of the Set, at {tracklist.set.duration} seconds.")

    for change in proposed:
        if isinstance(change, NameTrack):
            index = listed(change.play)
            before = plays[index]
            plays[index] = _named(before, change.track)
            made.append(Change(before, plays[index]))
        elif isinstance(change, AddPlay):
            within_set(change.timestamp)
            blend = None
            if change.blend_with is not None:
                other = listed(change.blend_with)
                blend = plays[other].blend
                if blend is None:
                    blend = max((p.blend for p in plays if p and p.blend is not None), default=0) + 1
                    plays[other] = plays[other].model_copy(update={"blend": blend})
            evidence = Evidence.LISTENER if change.track else None
            added = Play(timestamp=change.timestamp, track=change.track, evidence=evidence, blend=blend)
            plays.append(added)
            made.append(Change(None, added))
        elif isinstance(change, RemovePlay):
            index = listed(change.play)
            made.append(Change(plays[index], None))
            plays[index] = None
        else:
            within_set(change.timestamp)
            index = listed(change.play)
            blend = plays[index].blend
            for i, play in enumerate(plays):
                if i == index or (play is not None and blend is not None and play.blend == blend):
                    plays[i] = play.model_copy(update={"timestamp": change.timestamp})
                    made.append(Change(play, plays[i]))

    return in_order([play for play in plays if play is not None]), made


def _named(play: Play, track: Track | None) -> Play:
    """The Play as the listener named it, noting the Track it replaces."""
    if play.track is not None and track is not None and track.same_as(play.track):
        notes = play.notes
    elif play.track is not None:
        notes = [f"Was {play.track}"]
    else:
        notes = []
    evidence = Evidence.LISTENER if track else None
    return play.model_copy(update={"track": track, "evidence": evidence, "notes": notes})
