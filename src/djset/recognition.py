"""Clips, Recognitions, and the recognition service behind a single swap point."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiohttp
from pydantic import BaseModel
from shazamio import Shazam
from shazamio.interfaces.client import HTTPClientInterface
from shazamio_core import SearchParams, SignatureError

from djset.tracklist import Track


@dataclass(frozen=True, order=True)
class Clip:
    """A short stretch of a Set's audio sent for recognition."""

    start: int
    length: int


class Recognition(BaseModel):
    """What the recognition service reported for one Clip: a Track, or nothing."""

    start: int
    length: int
    track: Track | None

    @property
    def clip(self) -> Clip:
        return Clip(self.start, self.length)


class Refused(Exception):
    """The recognition service turned a request away (rate limit, block or outage). Worth retrying later."""


class Recogniser(Protocol):
    async def recognise(self, clip: Clip, wav: Path) -> Track | None:
        """The Track heard in the Clip's audio (a WAV file), or None. Raises Refused."""
        ...


class ShazamRecogniser:
    """Shazam through the unofficial shazamio library."""

    def __init__(self, http_client: HTTPClientInterface | None = None) -> None:
        self._shazam = Shazam(http_client=http_client or _RefusingClient())

    async def recognise(self, clip: Clip, wav: Path) -> Track | None:
        options = SearchParams(segment_duration_seconds=clip.length)
        try:
            result = await self._shazam.recognize(str(wav), options=options)
        except SignatureError:
            return None  # audio Shazam can't fingerprint, which no retry would change
        track = result.get("track") if isinstance(result, dict) else None
        if not track or not track.get("title"):
            return None
        return Track(artist=track.get("subtitle", "").strip() or "Unknown artist", title=track["title"].strip())


class _RefusingClient(HTTPClientInterface):
    """shazamio's HTTP client retries refusals itself for minutes; this one reports them so the Scanner can pace."""

    async def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.request(method, url, **kwargs) as response:
                    if response.status != 200:
                        raise Refused(f"Shazam answered HTTP {response.status}")
                    return await response.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as error:
            raise Refused(f"Shazam couldn't be reached ({type(error).__name__})") from error
