"""Sends Clips to the Recogniser at the pace it accepts, saving every Recognition as it arrives."""

import math
import tempfile
from pathlib import Path

from djset.audio import SetAudio
from djset.clock import Clock
from djset.links import clock
from djset.progress import Progress
from djset.recognition import Clip, Recogniser, Recognition, Refused
from djset.store import SetStore

GRID_EVERY = 15
CLIP_LENGTH = 12
SHORTEST_CLIP = 4
FOLLOW_UPS_PER_HOUR = 100

FIRST_WAIT = 30.0
LONGEST_WAIT = 600.0
GIVE_UP_AFTER = 1800.0
FIRST_GAP = 2.0
LONGEST_GAP = 20.0


class GaveUp(RuntimeError):
    def __init__(self, refusing_for: float, reason: str):
        super().__init__(
            f"The recognition service kept refusing requests for {round(refusing_for / 60)} minutes ({reason}). "
            "Everything recognised so far is saved; run the same command later to carry on."
        )


class CeilingReached(Exception):
    def __init__(self, needed: int, left: int):
        self.needed = needed
        self.left = left
        super().__init__(f"{needed} new Clips needed but only {left} follow-up Clips left")


def follow_up_ceiling(duration: int) -> int:
    return math.ceil(FOLLOW_UPS_PER_HOUR * duration / 3600)


class Scanner:
    """Recognises Clips of one Set.

    Sends as fast as the Recogniser accepts. On a refusal it waits from 30 seconds, doubling up to 10 minutes,
    then carries on with a gap between requests that grows with each refusal. It gives up after 30 minutes of
    continuous refusals. Clips already saved are never sent again.
    """

    def __init__(
        self,
        audio: SetAudio,
        recogniser: Recogniser,
        store: SetStore,
        clock: Clock,
        progress: Progress,
        duration: int,
    ):
        self._audio = audio
        self._recogniser = recogniser
        self._store = store
        self._clock = clock
        self._progress = progress
        self.duration = duration
        self.known = store.recognitions()
        self.follow_ups_left = follow_up_ceiling(duration)
        self._gap = 0.0
        self._last_sent: float | None = None

    def clips(self, start: int, end: int, every: int, length: int) -> list[Clip]:
        """Clips starting every `every` seconds from `start` up to `end`, cut short at the end of the Set."""
        end = min(end, self.duration)
        clips = [Clip(s, min(length, self.duration - s)) for s in range(start, end, every)]
        return [clip for clip in clips if clip.length >= SHORTEST_CLIP]

    def grid(self) -> list[Clip]:
        return self.clips(0, self.duration, GRID_EVERY, CLIP_LENGTH)

    async def scan_grid(self) -> list[Recognition]:
        grid = self.grid()
        done = sum(clip in self.known for clip in grid)
        self._progress.clips(done, len(grid))
        for clip in grid:
            if clip not in self.known:
                await self._recognise(clip)
                done += 1
                self._progress.clips(done, len(grid))
        return [self.known[clip] for clip in grid]

    async def follow_up(self, clips: list[Clip]) -> list[Recognition]:
        """Recognise extra Clips chosen by the agent. Saved Clips are free; new ones count against the ceiling."""
        new = [clip for clip in dict.fromkeys(clips) if clip not in self.known]
        if len(new) > self.follow_ups_left:
            raise CeilingReached(len(new), self.follow_ups_left)
        for clip in new:
            await self._recognise(clip)
            self.follow_ups_left -= 1
        return [self.known[clip] for clip in clips]

    async def _recognise(self, clip: Clip) -> Recognition:
        wait = FIRST_WAIT
        refusing_since: float | None = None
        while True:
            await self._space_out()
            try:
                with tempfile.TemporaryDirectory(prefix="djset-") as folder:
                    wav = Path(folder) / "clip.wav"
                    self._audio.cut(clip, wav)
                    track = await self._recogniser.recognise(clip, wav)
            except Refused as refusal:
                now = self._clock.now()
                if refusing_since is None:
                    refusing_since = now
                refusing_for = now - refusing_since
                if refusing_for >= GIVE_UP_AFTER:
                    raise GaveUp(refusing_for, str(refusal)) from refusal
                self._progress.paused(wait, refusing_for, f"{refusal} at {clock(clip.start)}")
                await self._clock.sleep(wait)
                wait = min(wait * 2, LONGEST_WAIT)
                continue
            if refusing_since is not None:
                self._gap = min(max(self._gap * 2, FIRST_GAP), LONGEST_GAP)
            recognition = Recognition(start=clip.start, length=clip.length, track=track)
            self._store.add_recognition(recognition)
            self.known[clip] = recognition
            return recognition

    async def _space_out(self) -> None:
        if self._gap and self._last_sent is not None:
            remaining = self._last_sent + self._gap - self._clock.now()
            if remaining > 0:
                await self._clock.sleep(remaining)
        self._last_sent = self._clock.now()
