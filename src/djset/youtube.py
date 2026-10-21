"""The Set Source for YouTube, built on yt-dlp (which needs Deno for YouTube's player challenges)."""

import shutil
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from djset.audio import FfmpegAudio
from djset.comments import ROOT, Comment
from djset.identify import MissingTool, SetUnavailable
from djset.links import watch_url
from djset.tracklist import SetInfo

TOP_LEVEL_COMMENTS = 3000
REPLIES = 2000
"""Replies are fetched thread by thread in YouTube's "top" order until this many have arrived."""
REPLIES_PER_THREAD = 15

_AVAILABILITY = {
    "private": "This video is private.",
    "subscriber_only": "This video is for channel members only.",
    "premium_only": "This video is for YouTube Premium members only.",
}
_REASONS = [
    ("private video", "This video is private."),
    ("members-only", "This video is for channel members only."),
    ("join this channel", "This video is for channel members only."),
    ("confirm your age", "This video is age-restricted. Try again with --cookies-from-browser <browser>."),
    ("not a bot", "YouTube asked for a sign-in check. Try again with --cookies-from-browser <browser>."),
    ("removed", "This video has been removed."),
    ("terminated", "This video's channel has been closed."),
    ("unavailable", "This video is unavailable."),
]


class YouTube:
    def __init__(self, cookies_from_browser: str | None = None, extractor_args: dict[str, Any] | None = None):
        self._cookies_from_browser = cookies_from_browser
        self._extractor_args = extractor_args or {}

    def check(self, needs_audio: bool) -> None:
        needed = {"deno": "YouTube's player challenges"}
        if needs_audio:
            needed["ffmpeg"] = "cutting Clips from the Set's audio"
        missing = [f"{tool} (for {purpose})" for tool, purpose in needed.items() if shutil.which(tool) is None]
        if missing:
            names = " ".join(tool for tool in needed if shutil.which(tool) is None)
            raise MissingTool(f"djset needs {' and '.join(missing)}. Install with: brew install {names}")

    def set_info(self, video_id: str) -> SetInfo:
        info = self._extract(video_id)
        if reason := _AVAILABILITY.get(info.get("availability") or ""):
            raise SetUnavailable(reason)
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise SetUnavailable("This is a live stream. Run djset again once it has finished and been uploaded.")
        if not info.get("duration"):
            raise SetUnavailable(
                "YouTube didn't return this video's details, which usually means it wants a sign-in check. "
                "Try again with --cookies-from-browser <browser>."
            )
        return SetInfo(
            video_id=video_id,
            title=info.get("title") or video_id,
            channel=info.get("channel") or info.get("uploader") or "",
            duration=int(info["duration"]),
            description=info.get("description") or "",
        )

    def comments(self, video_id: str) -> list[Comment]:
        """The most-liked comments with replies under the top threads, plus up to 3,000 of the newest comments.

        YouTube's "top" order stops after about 1,000 comments and its newest-first order is capped here at 3,000,
        so together they cover both the comments people liked most (where tracklists end up) and recent ones.
        Returns top-level comments most liked first, each followed by its replies.
        """
        top = self._comments(video_id, "top", replies=REPLIES, replies_per_thread=REPLIES_PER_THREAD)
        newest = self._comments(video_id, "new", replies=0, replies_per_thread=0)
        by_id = {comment.id: comment for comment in [*newest, *top]}
        replies: dict[str, list[Comment]] = {}
        for comment in by_id.values():
            if comment.is_reply:
                replies.setdefault(comment.parent, []).append(comment)
        top_level = sorted((c for c in by_id.values() if not c.is_reply), key=lambda c: c.likes, reverse=True)
        return [c for parent in top_level for c in (parent, *replies.get(parent.id, []))]

    def audio(self, video_id: str, folder: Path) -> FfmpegAudio:
        kept = [p for p in sorted(folder.glob("audio.*")) if p.suffix not in (".part", ".ytdl")]
        if kept:
            return FfmpegAudio(kept[0])
        folder.mkdir(parents=True, exist_ok=True)
        options = {"format": "bestaudio/best", "outtmpl": str(folder / "audio.%(ext)s"), "noplaylist": True}
        with YoutubeDL(self._options(options)) as ydl:
            try:
                info = ydl.extract_info(watch_url(video_id), download=True)
            except DownloadError as error:
                raise SetUnavailable(_reason(error)) from error
            return FfmpegAudio(Path(ydl.prepare_filename(info)))

    def _comments(self, video_id: str, order: str, replies: int, replies_per_thread: int) -> list[Comment]:
        limits = ["all", str(TOP_LEVEL_COMMENTS), str(replies), str(replies_per_thread)]
        youtube_args = {**self._extractor_args.get("youtube", {}), "max_comments": limits, "comment_sort": [order]}
        info = self._extract(
            video_id, getcomments=True, extractor_args={**self._extractor_args, "youtube": youtube_args}
        )
        return [
            Comment(
                id=raw["id"],
                parent=raw.get("parent") or ROOT,
                author=raw.get("author") or "",
                text=raw.get("text") or "",
                likes=raw.get("like_count") or 0,
                pinned=bool(raw.get("is_pinned")),
                by_uploader=bool(raw.get("author_is_uploader")),
            )
            for raw in info.get("comments") or []
        ]

    def _extract(self, video_id: str, **options: Any) -> dict[str, Any]:
        """Metadata and comments only. These are available even when playback needs a sign-in, such as for
        age-restricted videos, so unplayable formats are not an error here; the audio download reports them."""
        defaults = {"skip_download": True, "ignore_no_formats_error": True}
        with YoutubeDL(self._options({**defaults, **options})) as ydl:
            try:
                return ydl.extract_info(watch_url(video_id), download=False)
            except DownloadError as error:
                raise SetUnavailable(_reason(error)) from error

    def _options(self, options: dict[str, Any]) -> dict[str, Any]:
        base: dict[str, Any] = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": _Silent()}
        if self._cookies_from_browser:
            base["cookiesfrombrowser"] = (self._cookies_from_browser,)
        if self._extractor_args:
            base["extractor_args"] = self._extractor_args
        return {**base, **options}


def _reason(error: DownloadError) -> str:
    message = str(error)
    lowered = message.lower()
    for clue, reason in _REASONS:
        if clue in lowered:
            return reason
    return f"YouTube couldn't provide this video: {message.removeprefix('ERROR: ')}"


class _Silent:
    """yt-dlp's errors arrive as exceptions; its log lines would only clutter the progress display."""

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass
