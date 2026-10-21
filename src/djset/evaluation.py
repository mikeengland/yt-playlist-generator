"""Scores identification against Sets with trusted reference tracklists, and compares AI models on them.

It uses the real services, so it costs time and money.

Measures, for each run:
- Found (recall): the share of reference entries the run lists with the same artist and title, ignoring version
  text, within 45 seconds.
- Precision: the share of the run's identified Plays that match a reference entry. Plays in stretches the
  reference doesn't name are left out.
- F1: the balance of the two.
- Timestamp error: how far a found entry's Play is from the reference time, in seconds.
- What the agent used: AI requests, tokens, cost, time and follow-up Clips.

Every model sees the same evidence: Recognitions on the 15-second grid are made once and shared, and a run's draft
holds only the grid plus that run's own follow-up Clips.
"""

import asyncio
import difflib
import json
import re
import statistics
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel
from pydantic_ai import ModelAPIError, UnexpectedModelBehavior
from rich.console import Console
from rich.table import Table

from djset.cli import TerminalProgress
from djset.draft import build_draft
from djset.identify import Boundaries, OutOfBudget, Request, RunStats, identify, plays_from_draft
from djset.model_choice import ModelUnavailable, chosen_model, load_model
from djset.recognition import ShazamRecogniser
from djset.scanner import GaveUp, grid_clips
from djset.store import SetStore, default_root
from djset.tracklist import Mode, Play, Track, Tracklist
from djset.youtube import YouTube

WINDOW = 45
TITLE_SIMILARITY = 0.9
RECOGNITION_ALONE = "recognition alone (no AI)"
"""The baseline row: the draft from grid Recognitions turned straight into Plays, as when the AI budget runs out."""


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
    found: int
    """Reference entries the run found."""
    named: int
    """Reference entries that name a Track."""
    correct: int
    """Identified Plays that match a reference entry."""
    wrong: int
    """Identified Plays that match none, outside stretches the reference doesn't name."""
    unknown: int
    plays: int
    timestamp_errors: list[int] = field(default_factory=list)
    """Seconds between each found entry and its closest matching Play."""

    @property
    def recall(self) -> float:
        return self.found / self.named if self.named else 0.0

    @property
    def precision(self) -> float:
        judged = self.correct + self.wrong
        return self.correct / judged if judged else 0.0

    @property
    def f1(self) -> float:
        total = self.recall + self.precision
        return 2 * self.recall * self.precision / total if total else 0.0

    def __add__(self, other: "Score") -> "Score":
        return Score(
            found=self.found + other.found,
            named=self.named + other.named,
            correct=self.correct + other.correct,
            wrong=self.wrong + other.wrong,
            unknown=self.unknown + other.unknown,
            plays=self.plays + other.plays,
            timestamp_errors=self.timestamp_errors + other.timestamp_errors,
        )


def score(reference: Reference, plays: list[Play]) -> Score:
    identified = [play for play in plays if play.track is not None]
    named = [entry for entry in reference.entries if entry.named]
    errors = []
    for entry in named:
        offsets = [abs(p.timestamp - entry.timestamp) for p in identified if _matches(p.track, p.timestamp, entry)]
        if offsets:
            errors.append(min(offsets))
    correct = wrong = 0
    for play in identified:
        if any(_matches(play.track, play.timestamp, entry) for entry in named):
            correct += 1
        elif not _in_unnamed_stretch(reference, play.timestamp):
            wrong += 1
    return Score(
        found=len(errors),
        named=len(named),
        correct=correct,
        wrong=wrong,
        unknown=len(plays) - len(identified),
        plays=len(plays),
        timestamp_errors=errors,
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


@dataclass
class Row:
    """One run of one model on one Set in one mode."""

    video_id: str
    mode: Mode
    model: str
    run: int
    score: Score | None = None
    stats: RunStats | None = None
    error: str | None = None
    tracklist: Tracklist | None = None


async def compare(
    chosen: list[Reference],
    models: list[str],
    modes: list[Mode],
    runs: int,
    boundaries_for: Callable[[str], Boundaries],
    on_row: Callable[[Row], None] = lambda row: None,
    keep_audio: bool = False,
) -> list[Row]:
    """Run every model `runs` times on every Set in every mode, plus the recognition-alone baseline.

    Models take turns within each round of runs, so a slow or refusing recognition service affects them alike.
    """
    rows = []
    for reference in chosen:
        for mode in modes:
            for run in range(1, runs + 1):
                for model in models:
                    boundaries = boundaries_for(model)
                    request = Request(reference.video_id, mode=mode, keep_audio=True, rerun=True, save=False)
                    row = Row(reference.video_id, mode, model, run)
                    try:
                        outcome = await identify(request, boundaries)
                    except (ModelAPIError, UnexpectedModelBehavior, OutOfBudget, GaveUp) as error:
                        row.error = f"{type(error).__name__}: {error}"
                    else:
                        row.score = score(reference, outcome.tracklist.plays)
                        row.stats = outcome.stats
                        row.tracklist = outcome.tracklist
                    rows.append(row)
                    on_row(row)
        store = SetStore(boundaries_for(models[0]).store_root, reference.video_id)
        if baseline := _recognition_alone(reference, store, modes):
            rows.append(baseline)
            on_row(baseline)
        if not keep_audio:
            store.delete_audio()
    return rows


def _recognition_alone(reference: Reference, store: SetStore, modes: list[Mode]) -> Row | None:
    info = store.load_set_info()
    if all(mode == Mode.COMMENTS_ONLY for mode in modes) or info is None:
        return None
    saved = store.recognitions()
    grid = [saved[clip] for clip in grid_clips(info.duration) if clip in saved]
    if not grid:
        return None
    plays = plays_from_draft(build_draft(grid))
    return Row(reference.video_id, Mode.AUDIO_ONLY, RECOGNITION_ALONE, 1, score=score(reference, plays))


@dataclass
class Spread:
    mean: float
    low: float
    high: float

    @classmethod
    def of(cls, values: list[float]) -> "Spread":
        return cls(statistics.fmean(values), min(values), max(values)) if values else cls(0.0, 0.0, 0.0)


@dataclass
class Summary:
    """One model in one mode, across every Set and run.

    Each run's Sets are pooled into one score; the spreads show how much the model varies between runs.
    """

    model: str
    mode: Mode
    runs: int
    failed: int
    f1: Spread
    recall: Spread
    precision: Spread
    wrong_per_set: float
    unknown_per_set: float
    median_timestamp_error: float | None
    cost_per_set: Decimal | None
    tokens_per_set: float
    seconds_per_set: float
    follow_up_clips_per_set: float
    out_of_budget: int


def summarise(rows: list[Row]) -> list[Summary]:
    summaries = []
    groups: dict[tuple[str, Mode], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.model, row.mode), []).append(row)
    for (model, mode), group in groups.items():
        by_run: dict[int, list[Row]] = {}
        for row in group:
            by_run.setdefault(row.run, []).append(row)
        complete = {run: rs for run, rs in by_run.items() if all(r.score for r in rs)}
        pooled = [sum((r.score for r in rs), Score(0, 0, 0, 0, 0, 0)) for rs in complete.values()]
        scored = [r for rs in complete.values() for r in rs]
        with_stats = [r.stats for r in scored if r.stats]
        costs = [s.cost for s in with_stats]
        errors = [e for p in pooled for e in p.timestamp_errors]
        sets = len(scored) or 1
        summaries.append(
            Summary(
                model=model,
                mode=mode,
                runs=len(complete),
                failed=len(by_run) - len(complete),
                f1=Spread.of([p.f1 for p in pooled]),
                recall=Spread.of([p.recall for p in pooled]),
                precision=Spread.of([p.precision for p in pooled]),
                wrong_per_set=sum(p.wrong for p in pooled) / sets,
                unknown_per_set=sum(p.unknown for p in pooled) / sets,
                median_timestamp_error=statistics.median(errors) if errors else None,
                cost_per_set=sum(costs, Decimal(0)) / len(costs) if costs and None not in costs else None,
                tokens_per_set=sum(s.input_tokens + s.output_tokens for s in with_stats) / sets,
                seconds_per_set=sum(s.seconds for s in with_stats) / sets,
                follow_up_clips_per_set=sum(s.follow_up_clips for s in with_stats) / sets,
                out_of_budget=sum(s.out_of_budget for s in with_stats),
            )
        )
    return sorted(summaries, key=lambda s: (list(Mode).index(s.mode), -s.f1.mean))


def _spread(spread: Spread, runs: int) -> str:
    text = f"{spread.mean:.0%}"
    return f"{text} ({spread.low:.0%}–{spread.high:.0%})" if runs > 1 else text


def _cost(cost: Decimal | None) -> str:
    return f"${cost:.2f}" if cost is not None else "unknown"


SUMMARY_COLUMNS = [
    "Model", "Mode", "F1", "Found", "Precision", "Wrong", "Unknown", "Timing", "Cost", "Tokens", "Time", "Runs",
]  # fmt: skip


def summary_cells(s: Summary) -> list[str]:
    if s.model == RECOGNITION_ALONE:
        usage = ["–", "–", "–"]
    else:
        usage = [_cost(s.cost_per_set), f"{s.tokens_per_set / 1000:,.0f}k", f"{s.seconds_per_set:.0f}s"]
    timing = f"{s.median_timestamp_error:.0f}s" if s.median_timestamp_error is not None else "–"
    runs = f"{s.runs}" + (f" ({s.failed} failed)" if s.failed else "")
    return [
        s.model,
        s.mode.value,
        _spread(s.f1, s.runs),
        _spread(s.recall, s.runs),
        _spread(s.precision, s.runs),
        f"{s.wrong_per_set:.1f}",
        f"{s.unknown_per_set:.1f}",
        timing,
        *usage,
        runs,
    ]


def report(rows: list[Row], chosen: list[Reference], started: datetime) -> str:
    """The comparison as Markdown."""
    summaries = summarise(rows)
    lines = [
        f"# djset evaluation, {started:%Y-%m-%d %H:%M}",
        "",
        "Sets: " + ", ".join(f"{r.title} (`{r.video_id}`)" for r in chosen),
        "",
        "| " + " | ".join(SUMMARY_COLUMNS) + " |",
        "|" + " --- |" * len(SUMMARY_COLUMNS),
        *("| " + " | ".join(summary_cells(s)) + " |" for s in summaries),
        "",
        "F1, Found and Precision are means across runs, with the lowest and highest run in brackets. Wrong, Unknown,"
        " Cost, Tokens and Time are per Set run. Timing is the median distance from the reference Timestamp.",
        f"`{RECOGNITION_ALONE}` is the draft from grid Recognitions with no agent: what the AI adds is the difference.",
        "In normal mode the agent can read the comment the reference was taken from, so it mostly measures how well"
        " the model reads comments; audio-only mode measures its judgement over Recognitions.",
        "",
        "## Each Set",
        "",
        "| Set | Model | Mode | Run | Found | Wrong | Unknown | Cost | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if row.score:
            found = f"{row.score.found}/{row.score.named}"
            cells = [found, str(row.score.wrong), str(row.score.unknown)]
        else:
            cells = ["–", "–", "–"]
        cost = _cost(row.stats.cost) if row.stats else "–"
        note = row.error or ("AI budget ran out" if row.stats and row.stats.out_of_budget else "")
        lines.append(
            f"| `{row.video_id}` | {row.model} | {row.mode.value} | {row.run} | "
            + " | ".join(cells)
            + f" | {cost} | {note} |"
        )
    return "\n".join(lines) + "\n"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


def save(rows: list[Row], chosen: list[Reference], started: datetime, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for row in rows:
        if row.tracklist:
            name = f"{row.video_id}-{_slug(row.mode.value)}-{_slug(row.model)}-run{row.run}.json"
            (folder / "tracklists").mkdir(exist_ok=True)
            (folder / "tracklists" / name).write_text(row.tracklist.model_dump_json(indent=2))
    data = [
        {
            **asdict(row, dict_factory=_without_tracklist),
            **({"recall": row.score.recall, "precision": row.score.precision, "f1": row.score.f1} if row.score else {}),
        }
        for row in rows
    ]
    (folder / "runs.json").write_text(json.dumps(data, indent=2, default=str))
    path = folder / "report.md"
    path.write_text(report(rows, chosen, started))
    return path


def _without_tracklist(items: list[tuple]) -> dict:
    return {key: value for key, value in items if key != "tracklist"}


app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
MODES = {"audio-only": Mode.AUDIO_ONLY, "normal": Mode.NORMAL, "comments-only": Mode.COMMENTS_ONLY}


@app.command()
def evaluate(
    video_ids: Annotated[
        list[str] | None, typer.Argument(help="Sets to evaluate. Default: every reference Set.")
    ] = None,
    model: Annotated[
        list[str] | None,
        typer.Option(
            metavar="PROVIDER:MODEL", help="A model to compare; repeat for several. Default: the usual model."
        ),
    ] = None,
    mode: Annotated[
        list[str] | None, typer.Option(help="audio-only, normal or comments-only; repeat for several.")
    ] = None,
    runs: Annotated[int, typer.Option(min=1, help="Runs per model, Set and mode, to show how much results vary.")] = 3,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Fetch and recognise the Sets again instead of reusing saved evidence.")
    ] = False,
    keep_audio: Annotated[
        bool, typer.Option("--keep-audio", help="Keep the Sets' audio for later evaluations.")
    ] = False,
    cookies_from_browser: Annotated[str | None, typer.Option(metavar="BROWSER")] = None,
) -> None:
    """Compare AI models on Sets with trusted reference tracklists."""
    models = model or [chosen_model(None)]
    try:
        modes = [MODES[m] for m in mode or ["audio-only", "normal"]]
    except KeyError as error:
        raise typer.BadParameter(f"unknown mode {error}; use audio-only, normal or comments-only") from error
    chosen = [r for r in references() if not video_ids or r.video_id in video_ids]
    console = Console()
    for name in models:
        try:
            load_model(name)  # a missing API key should stop the evaluation now, not an hour in
        except ModelUnavailable as error:
            Console(stderr=True).print(f"[red]{error}[/red]", highlight=False)
            raise typer.Exit(1) from error
    if fresh:
        for reference in chosen:
            SetStore(default_root(), reference.video_id).forget_evidence()
    total = len(chosen) * len(modes) * len(models) * runs
    console.print(f"{total} runs: {len(chosen)} Sets × {len(modes)} modes × {len(models)} models × {runs} runs")

    progress = TerminalProgress(console)
    source = YouTube(cookies_from_browser=cookies_from_browser)
    recogniser = ShazamRecogniser()

    def boundaries_for(name: str) -> Boundaries:
        return Boundaries(set_source=source, recogniser=recogniser, model=name, progress=progress)

    def on_row(row: Row) -> None:
        progress.close()
        if row.error:
            console.print(f"[red]{row.video_id} {row.mode.value} {row.model} run {row.run}: {row.error}[/red]")
        elif row.score:
            usage = f", {_cost(row.stats.cost)}, {row.stats.seconds:.0f}s" if row.stats else ""
            console.print(
                f"{row.video_id} {row.mode.value} {row.model} run {row.run}: found {row.score.found}/{row.score.named},"
                f" {row.score.wrong} wrong{usage}",
                highlight=False,
            )

    started = datetime.now()
    rows = asyncio.run(compare(chosen, models, modes, runs, boundaries_for, on_row, keep_audio))
    progress.close()

    table = Table(*SUMMARY_COLUMNS)
    for summary in summarise(rows):
        table.add_row(*summary_cells(summary))
    console.print(table)
    path = save(rows, chosen, started, default_root() / "evaluations" / f"{started:%Y-%m-%d-%H%M%S}")
    console.print(f"Report: {path}", style="dim", highlight=False)


def main() -> None:
    app()
