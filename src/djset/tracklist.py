"""The Tracklist a run produces, saved as versioned JSON for the later Spotify step."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, Field, computed_field, model_validator

from djset.links import watch_url

SCHEMA_VERSION = 1


class Track(BaseModel):
    """One specific recording, down to its version."""

    artist: str
    title: str
    version: str | None = Field(
        default=None,
        description="The edit or remix, e.g. 'FNKY Remix'. Leave empty for the Original.",
    )

    def __str__(self) -> str:
        name = f"{self.artist} – {self.title}"
        return f"{name} ({self.version})" if self.version else name

    def same_as(self, other: "Track") -> bool:
        return self.key() == other.key()

    def key(self) -> tuple[str, str, str]:
        """What makes two Tracks the same, ignoring case and surrounding spaces."""
        return (self.artist.casefold().strip(), self.title.casefold().strip(), (self.version or "").casefold().strip())


class Evidence(StrEnum):
    """What a Play's identification rests on."""

    CONFIRMED = "confirmed"
    AUDIO_ONLY = "audio only"
    COMMENTS_ONLY = "comments only"


class Play(BaseModel):
    """One occurrence of a Track in a Set."""

    timestamp: int = Field(ge=0, description="Seconds into the Set where the Play is first heard.")
    track: Track | None = Field(description="The Track, or null for an Unknown Track.")
    evidence: Evidence | None = Field(description="What the identification rests on; null for an Unknown Track.")
    blend: int | None = Field(
        default=None,
        description="A number shared by the Plays of one Blend (heard at the same time); null otherwise.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Short notes for the listener, such as a Conflicting Hint or the edit a comment named.",
    )

    @model_validator(mode="after")
    def _evidence_matches_track(self) -> Self:
        if (self.track is None) != (self.evidence is None):
            raise ValueError("an Unknown Track has no evidence, and an identified Play must have evidence")
        return self


class Mode(StrEnum):
    """Which sources a run identifies Plays from."""

    NORMAL = "normal"
    COMMENTS_ONLY = "comments only"
    AUDIO_ONLY = "audio only"


class SetInfo(BaseModel):
    """One recorded DJ performance in a single YouTube video."""

    video_id: str
    title: str
    channel: str
    duration: int = Field(description="Length in seconds.")
    description: str = ""

    @computed_field
    @property
    def url(self) -> str:
        return watch_url(self.video_id)


class Tracklist(BaseModel):
    """The Plays of a Set in order."""

    schema_version: Literal[1] = SCHEMA_VERSION
    set: SetInfo
    mode: Mode
    plays: list[Play]
