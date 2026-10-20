"""Scores identification against Sets with trusted reference tracklists.

It uses the real services, so it costs time and money.

A reference entry is found when the run lists the same artist and title, ignoring version text, within 45 seconds.
Stretches the reference doesn't name are left out of the score.
"""

import asyncio
import difflib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib import resources
from typing import Annotated

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from djset.cli import TerminalProgress
from djset.identify import Boundaries, Request, identify
from djset.model_choice import chosen_model
from djset.recognition import ShazamRecogniser
from djset.store import default_root
from djset.tracklist import Mode, Track, Tracklist
from djset.youtube import YouTube

WINDOW = 45
TITLE_SIMILARITY = 0.9


class ReferenceEntry(BaseModel):
    timestamp: int
    artist: str | None
    title: str | None
    version: str | None = None
    also: list[str] = []
    """Other titles the same Track goes by, such as a clean or explicit title, or an abbreviation."""
    note: str | None = None

    @property
    def named(self) -> bool:
        return self.title is not None


class Reference(BaseModel):
    video_id: str
    title: str
    source: str
    entries: list[ReferenceEntry]


def references() -> list[Reference]:
    folder = resources.files("djset") / "references"
    files = sorted(f for f in folder.iterdir() if f.name.endswith(".json"))
    return [Reference.model_validate_json(f.read_text()) for f in files]


@dataclass
class Score:
    video_id: str
    mode: str
    found: int
    named: int
    wrong: int
    unknown: int
    plays: int

    @property
    def share_found(self) -> float:
        return self.found / self.named if self.named else 0.0


def score(reference: Reference, tracklist: Tracklist) -> Score:
    identified = [play for play in tracklist.plays if play.track is not None]
    named = [entry for entry in reference.entries if entry.named]
    found = sum(any(_matches(play.track, play.timestamp, entry) for play in identified) for entry in named)
    wrong = sum(
        not any(_matches(play.track, play.timestamp, entry) for entry in named)
        and not _in_unnamed_stretch(reference, play.timestamp)
        for play in identified
    )
    return Score(
        video_id=reference.video_id,
        mode=tracklist.mode.value,
        found=found,
        named=len(named),
        wrong=wrong,
        unknown=len(tracklist.plays) - len(identified),
        plays=len(tracklist.plays),
    )


def _matches(track: Track | None, timestamp: int, entry: ReferenceEntry) -> bool:
    if track is None or not entry.named or abs(timestamp - entry.timestamp) > WINDOW:
        return False
    titles = {_title(t) for t in [entry.title, *entry.also] if t}
    title = _title(track.title)
    if not any(title == t or difflib.SequenceMatcher(None, title, t).ratio() >= TITLE_SIMILARITY for t in titles):
        return False
    return not entry.artist or bool(_artists(track.artist) & _artists(entry.artist))


def _in_unnamed_stretch(reference: Reference, timestamp: int) -> bool:
    current = None
    for entry in reference.entries:
        if entry.timestamp <= timestamp:
            current = entry
    return current is not None and not current.named


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return text.replace("&", " and ")


def _title(title: str) -> str:
    title = _plain(title)
    title = re.sub(r"[(\[].*?[)\]]", " ", title)
    title = re.split(r"\s(?:feat\.?|ft\.?|featuring)\s", f" {title} ")[0]
    return " ".join(re.sub(r"[^a-z0-9 ]", "", title).split())


def _artists(artist: str) -> set[str]:
    artist = re.sub(r"[(\[].*?[)\]]", " ", _plain(artist))
    names = re.split(r",|\band\b|\bx\b|\bvs\.?|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b", artist)
    cleaned = {" ".join(re.sub(r"[^a-z0-9 ]", "", name).split()) for name in names}
    return {name.removeprefix("the ") for name in cleaned if name}


app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@app.command()
def evaluate(
    video_ids: Annotated[
        list[str] | None, typer.Argument(help="Sets to evaluate. Default: every reference Set.")
    ] = None,
    mode: Annotated[str, typer.Option(help="audio-only, normal or both.")] = "both",
    model: Annotated[str | None, typer.Option(metavar="PROVIDER:MODEL", help="The AI model.")] = None,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Recognise the Sets again instead of reusing Recognitions.")
    ] = False,
    cookies_from_browser: Annotated[str | None, typer.Option(metavar="BROWSER")] = None,
) -> None:
    """Score djset against Sets with trusted reference tracklists, with comments hidden and in normal mode."""
    modes = {"audio-only": [Mode.AUDIO_ONLY], "normal": [Mode.NORMAL], "both": [Mode.AUDIO_ONLY, Mode.NORMAL]}[mode]
    chosen = [r for r in references() if not video_ids or r.video_id in video_ids]
    console = Console()
    progress = TerminalProgress(console)
    boundaries = Boundaries(
        set_source=YouTube(cookies_from_browser=cookies_from_browser),
        recogniser=ShazamRecogniser(),
        model=chosen_model(model),
        progress=progress,
    )
    scores = []
    for reference in chosen:
        for index, run_mode in enumerate(modes):
            console.print(f"{reference.title} ({run_mode.value})")
            request = Request(
                link=reference.video_id,
                mode=run_mode,
                fresh=fresh and index == 0,
                keep_audio=index < len(modes) - 1,
                rerun=True,
            )
            try:
                outcome = asyncio.run(identify(request, boundaries))
            finally:
                progress.close()
            scores.append(score(reference, outcome.tracklist))

    table = Table("Set", "Mode", "Found", "Wrong", "Unknown", "Plays")
    for s in scores:
        table.add_row(
            s.video_id, s.mode, f"{s.found}/{s.named} ({s.share_found:.0%})", str(s.wrong), str(s.unknown), str(s.plays)
        )
    console.print(table)
    folder = default_root() / "evaluations"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y-%m-%d-%H%M%S}.json"
    path.write_text(json.dumps({"model": chosen_model(model), "scores": [asdict(s) for s in scores]}, indent=2))
    console.print(f"Saved to {path}", style="dim")


def main() -> None:
    app()
