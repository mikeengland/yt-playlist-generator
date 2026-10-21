"""Turns Recognitions into a draft Tracklist for the agent to check and complete."""

from dataclasses import dataclass, field
from itertools import combinations

from djset.links import clock
from djset.recognition import Recognition
from djset.tracklist import Track

MERGE_WINDOW = 45
"""Recognitions of the same Track this close together (in seconds) belong to one Play."""


@dataclass
class DraftPlay:
    timestamp: int
    last_heard: int
    track: Track
    clips: int = 1
    overlaps: list[Track] = field(default_factory=list)
    """Tracks recognised within this Play's stretch: a possible Blend, or a one-off Recognition."""

    @property
    def lone(self) -> bool:
        return self.clips == 1


@dataclass
class Gap:
    start: int
    end: int


@dataclass
class Draft:
    entries: list[DraftPlay | Gap]
    clip_count: int

    @property
    def plays(self) -> list[DraftPlay]:
        return [entry for entry in self.entries if isinstance(entry, DraftPlay)]

    @property
    def gaps(self) -> list[Gap]:
        return [entry for entry in self.entries if isinstance(entry, Gap)]

    def text(self) -> str:
        """The draft as compact text for the agent."""
        lines = [f"Draft from {self.clip_count} Clips. Times are when a Clip starts."]
        for entry in self.entries:
            if isinstance(entry, Gap):
                lines.append(f"{clock(entry.start)}–{clock(entry.end)}  nothing recognised")
                continue
            span = clock(entry.timestamp)
            if entry.last_heard > entry.timestamp:
                span += f"–{clock(entry.last_heard)}"
            flags = ["lone: heard by 1 Clip" if entry.lone else f"{entry.clips} Clips"]
            if entry.overlaps:
                flags.append("overlaps " + "; ".join(str(track) for track in entry.overlaps) + " (possible Blend)")
            lines.append(f"{span}  {entry.track}  [{', '.join(flags)}]")
        return "\n".join(lines)


def build_draft(recognitions: list[Recognition]) -> Draft:
    """Merge Recognitions of one Track into Plays, flag lone and overlapping Plays, and mark gaps."""
    ordered = sorted(recognitions, key=lambda r: (r.start, r.length))
    plays: list[DraftPlay] = []
    for recognition in ordered:
        if recognition.track is None:
            continue
        play = next((p for p in reversed(plays) if p.track.same_as(recognition.track)), None)
        if play and recognition.start - play.last_heard <= MERGE_WINDOW:
            play.last_heard = recognition.start
            play.clips += 1
        else:
            plays.append(DraftPlay(timestamp=recognition.start, last_heard=recognition.start, track=recognition.track))

    for a, b in combinations(plays, 2):
        if a.timestamp <= b.last_heard and b.timestamp <= a.last_heard:
            a.overlaps.append(b.track)
            b.overlaps.append(a.track)

    gaps: list[Gap] = []
    current: Gap | None = None
    for recognition in ordered:
        covered = any(p.timestamp <= recognition.start <= p.last_heard for p in plays)
        if recognition.track is not None or covered:
            current = None
            continue
        end = recognition.start + recognition.length
        if current is None:
            current = Gap(recognition.start, end)
            gaps.append(current)
        else:
            current.end = max(current.end, end)

    entries: list[DraftPlay | Gap] = [*plays, *gaps]
    entries.sort(key=lambda e: e.timestamp if isinstance(e, DraftPlay) else e.start)
    return Draft(entries=entries, clip_count=len(ordered))
