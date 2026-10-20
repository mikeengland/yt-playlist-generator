"""Time, behind a boundary so tests can let pacing waits pass instantly."""

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Seconds on a monotonic clock."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
