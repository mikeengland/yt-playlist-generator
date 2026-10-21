"""What a run reports while it works, so a 15–20 minute run visibly keeps going."""

from typing import Protocol


class Progress(Protocol):
    def step(self, message: str) -> None:
        """A new stage of the run has started."""
        ...

    def clips(self, done: int, total: int) -> None:
        """Clips on the 15-second grid recognised so far."""
        ...

    def paused(self, wait: float, refusing_for: float, reason: str) -> None:
        """The recognition service refused a request; the run waits before retrying."""
        ...

    def note(self, message: str) -> None: ...


class SilentProgress:
    def step(self, message: str) -> None:
        pass

    def clips(self, done: int, total: int) -> None:
        pass

    def paused(self, wait: float, refusing_for: float, reason: str) -> None:
        pass

    def note(self, message: str) -> None:
        pass
