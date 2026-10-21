"""The `djset` command."""

import asyncio
import re
from typing import Annotated

import typer
from pydantic_ai import ModelAPIError, UnexpectedModelBehavior
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.status import Status

from djset.audio import AudioError
from djset.identify import Boundaries, MissingTool, OutOfBudget, Request, RunStats, SetUnavailable, identify
from djset.links import InvalidLink
from djset.model_choice import SETTING, ModelUnavailable, chosen_model
from djset.playlist import PlaylistBoundaries, PlaylistOutcome, Proposal, Suggestion, make_playlist
from djset.recognition import ShazamRecogniser
from djset.render import print_proposal, print_tracklist
from djset.scanner import GaveUp
from djset.spotify import TOKEN_FILE, BrowserSignIn, SpotifyBusy, SpotifyUnavailable, SpotifyWebApi, client_id
from djset.tracklist import Mode
from djset.youtube import YouTube

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
console = Console()
errors = Console(stderr=True)


@app.command()
def djset(
    link: Annotated[str, typer.Argument(help="The YouTube Set's link, in any form.")],
    fresh: Annotated[bool, typer.Option("--fresh", help="Ignore saved results and identify the Set again.")] = False,
    keep_audio: Annotated[bool, typer.Option("--keep-audio", help="Keep the downloaded audio for re-runs.")] = False,
    comments_only: Annotated[
        bool, typer.Option("--comments-only", help="Use only the description and comments: no audio, about a minute.")
    ] = False,
    cookies_from_browser: Annotated[
        str | None,
        typer.Option(metavar="BROWSER", help="Use this browser's YouTube sign-in, e.g. chrome, safari or firefox."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            metavar="PROVIDER:MODEL", help=f"The AI model. Default: {SETTING} or anthropic:claude-sonnet-5-5."
        ),
    ] = None,
    spotify: Annotated[
        bool, typer.Option("--spotify", help="Then make a private Spotify playlist of the Tracks you choose.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="With --spotify, add every Track found on Spotify without asking.")
    ] = False,
) -> None:
    """Work out which Tracks a DJ plays in a YouTube Set, and when, and optionally make a Spotify playlist of them."""
    request = Request(
        link=link,
        mode=Mode.COMMENTS_ONLY if comments_only else Mode.NORMAL,
        fresh=fresh,
        keep_audio=keep_audio,
    )
    progress = TerminalProgress(console)
    boundaries = Boundaries(
        set_source=YouTube(cookies_from_browser=cookies_from_browser),
        recogniser=ShazamRecogniser(),
        model=chosen_model(model),
        progress=progress,
    )
    spotify_api = None
    if spotify:
        try:
            spotify_api = SpotifyWebApi(client_id(), boundaries.store_root / TOKEN_FILE, BrowserSignIn(progress.note))
        except SpotifyUnavailable as error:
            _fail(str(error))
    try:
        asyncio.run(_run(request, boundaries, spotify_api, TerminalChooser(console, progress, add_all=yes)))
    except KeyboardInterrupt:
        progress.close()
        _fail("Stopped. Everything found so far is saved; run the same command to carry on.")
    except (
        InvalidLink,
        SetUnavailable,
        MissingTool,
        ModelUnavailable,
        GaveUp,
        OutOfBudget,
        AudioError,
        SpotifyUnavailable,
        SpotifyBusy,
    ) as error:
        progress.close()
        _fail(str(error))
    except ModelAPIError as error:
        progress.close()
        _fail(f"The AI model's service failed: {error}. Recognitions are saved; run the same command to carry on.")
    except UnexpectedModelBehavior as error:
        progress.close()
        _fail(
            f"The AI model didn't return a usable Tracklist ({error}). "
            "Recognitions are saved; run the same command to try again."
        )


async def _run(
    request: Request, boundaries: Boundaries, spotify: SpotifyWebApi | None, chooser: "TerminalChooser"
) -> None:
    progress = boundaries.progress
    try:
        if spotify:
            await spotify.sign_in()
        outcome = await identify(request, boundaries)
        progress.close()
        console.print()
        print_tracklist(console, outcome.tracklist)
        console.print()
        if outcome.reused:
            console.print("Saved from an earlier run. Use --fresh to identify the Set again.", style="dim")
        if outcome.stats:
            console.print(usage_line(outcome.stats), style="dim", highlight=False)
        console.print(f"Saved to {outcome.markdown_path}", style="dim", highlight=False)
        if spotify:
            playlist_boundaries = PlaylistBoundaries(
                spotify=spotify, chooser=chooser, progress=progress, store_root=boundaries.store_root
            )
            made = await make_playlist(outcome.tracklist, playlist_boundaries)
            progress.close()
            console.print()
            console.print(playlist_line(made), highlight=False)
    finally:
        if spotify:
            await spotify.aclose()


def playlist_line(outcome: PlaylistOutcome) -> str:
    playlist = outcome.playlist
    if playlist is None:
        return "No Spotify playlist made."
    if outcome.reused:
        return f"This Set's Spotify playlist is already made: {playlist.url}"
    count = len(playlist.tracks)
    tracks = f"{count} Track{'' if count == 1 else 's'}"
    return f"Made the private Spotify playlist {playlist.name!r} with {tracks}: {playlist.url}"


class TerminalChooser:
    """Shows the proposed Playlist and asks which Plays to leave out, unless told to add them all."""

    def __init__(self, console: Console, progress: "TerminalProgress", add_all: bool):
        self._console = console
        self._progress = progress
        self._add_all = add_all

    def choose(self, proposal: Proposal) -> list[Suggestion]:
        self._progress.close()
        self._console.print()
        print_proposal(self._console, proposal)
        if self._add_all or not proposal.suggestions:
            return proposal.suggestions
        prompt = (
            f"Press Enter to add all {len(proposal.suggestions)}, type numbers to leave out (like 3 7-9), "
            "or n to cancel: "
        )
        while True:
            try:
                return leave_out(proposal.suggestions, self._console.input(prompt))
            except ValueError as error:
                self._console.print(f"[red]{error}[/red]", highlight=False)
            except EOFError:
                return []


def leave_out(suggestions: list[Suggestion], answer: str) -> list[Suggestion]:
    """The suggestions left after the listener's answer: Enter keeps all, numbers and ranges leave some out, n none."""
    answer = answer.strip().casefold()
    if answer in ("", "y", "yes"):
        return suggestions
    if answer in ("n", "no", "q"):
        return []
    left_out: set[int] = set()
    for part in re.split(r"[\s,]+", re.sub(r"\s*[-–]\s*", "-", answer)):
        numbers = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not numbers:
            raise ValueError(f"{part!r} isn't a number or a range like 7-9.")
        first, last = int(numbers[1]), int(numbers[2] or numbers[1])
        if not 1 <= first <= last <= len(suggestions):
            raise ValueError(f"{part} isn't within 1-{len(suggestions)}.")
        left_out.update(range(first, last + 1))
    return [suggestion for suggestion in suggestions if suggestion.number not in left_out]


def usage_line(stats: RunStats) -> str:
    cost = f"about ${stats.cost:.2f}" if stats.cost is not None else "cost unknown for this model"
    tokens = stats.input_tokens + stats.output_tokens
    return f"{stats.model}: {stats.requests} AI requests, {tokens / 1000:,.0f}k tokens, {cost}"


def _fail(message: str) -> None:
    errors.print(f"[red]{message}[/red]", highlight=False)
    raise typer.Exit(1)


class TerminalProgress:
    """Shows one live line at a time: a spinner for the current step, or a bar while recognising Clips."""

    def __init__(self, console: Console):
        self._console = console
        self._status: Status | None = None
        self._bar: Progress | None = None

    def step(self, message: str) -> None:
        self.close()
        self._status = self._console.status(message)
        self._status.start()

    def clips(self, done: int, total: int) -> None:
        if self._bar is None:
            self.close()
            self._bar = Progress(
                TextColumn("Recognising Clips"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=self._console,
            )
            self._bar.add_task("clips", total=total)
            self._bar.start()
        self._bar.update(self._bar.task_ids[0], completed=done, total=total)

    def paused(self, wait: float, refusing_for: float, reason: str) -> None:
        refusing = f", refusing for {_duration(refusing_for)}" if refusing_for else ""
        self._console.print(f"[yellow]{reason}. Waiting {_duration(wait)} before retrying{refusing}.[/yellow]")

    def note(self, message: str) -> None:
        self._console.print(message, style="dim", highlight=False, soft_wrap=True)

    def close(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
        if self._bar is not None:
            self._bar.stop()
            self._bar = None


def _duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def main() -> None:
    app()
