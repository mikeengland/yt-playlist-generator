"""The Tracklist for people: a terminal table and a Markdown file, with a link to every Timestamp."""

from collections import Counter

from rich.console import Console
from rich.table import Table
from rich.text import Text

from djset.links import clock, watch_url
from djset.tracklist import Evidence, Mode, Play, Tracklist

EVIDENCE_LABELS = {
    Evidence.CONFIRMED: "Confirmed",
    Evidence.AUDIO_ONLY: "audio only",
    Evidence.COMMENTS_ONLY: "comments only",
}
UNKNOWN = "Unknown Track"


def track_text(play: Play) -> str:
    return str(play.track) if play.track else UNKNOWN


def based_on(play: Play) -> str:
    return EVIDENCE_LABELS[play.evidence] if play.evidence else ""


def notes_text(play: Play) -> str:
    return "; ".join((["Blend"] if play.blend is not None else []) + play.notes)


def summary(tracklist: Tracklist) -> str:
    counts = Counter(based_on(play) or UNKNOWN for play in tracklist.plays)
    parts = [f"{counts[label]} {label}" for label in [*EVIDENCE_LABELS.values(), UNKNOWN] if counts[label]]
    count = len(tracklist.plays)
    return f"{count} Play{'' if count == 1 else 's'}: {', '.join(parts)}" if parts else "No Plays"


def _mode_note(tracklist: Tracklist) -> str:
    return {
        Mode.NORMAL: "",
        Mode.COMMENTS_ONLY: " · from the description and comments only",
        Mode.AUDIO_ONLY: " · from audio only (comments hidden)",
    }[tracklist.mode]


def markdown(tracklist: Tracklist) -> str:
    info = tracklist.set
    lines = [
        f"# {info.title}",
        "",
        f"{info.channel} · [Watch on YouTube]({info.url}) · {clock(info.duration)}{_mode_note(tracklist)}",
        "",
        summary(tracklist),
        "",
        "| Time | Track | Based on | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for play in tracklist.plays:
        link = f"[{clock(play.timestamp)}]({watch_url(info.video_id, play.timestamp)})"
        cells = [link, track_text(play), based_on(play), notes_text(play)]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def print_tracklist(console: Console, tracklist: Tracklist) -> None:
    info = tracklist.set
    console.print(f"[bold]{info.title}[/bold]")
    console.print(f"{info.channel} · {info.url} · {clock(info.duration)}{_mode_note(tracklist)}", highlight=False)
    table = Table(show_edge=False, pad_edge=False, header_style="bold")
    table.add_column("Time", justify="right", no_wrap=True)
    table.add_column("Track", ratio=3)
    table.add_column("Based on", no_wrap=True)
    table.add_column("Notes", ratio=2)
    table.add_column("Link", no_wrap=True, overflow="fold")
    for play in tracklist.plays:
        url = watch_url(info.video_id, play.timestamp)
        style = "dim" if play.track is None else ""
        table.add_row(
            Text(clock(play.timestamp), style=f"link {url}"),
            Text(track_text(play), style=style),
            based_on(play),
            notes_text(play),
            Text(url, style="dim"),
        )
    console.print(table)
    console.print(summary(tracklist))
