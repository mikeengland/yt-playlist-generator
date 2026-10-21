"""YouTube links: reading a video ID out of any link form, and building links to a Timestamp."""

import re
from urllib.parse import parse_qs, urlparse

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH_FORMS = {"live", "shorts", "embed", "v", "e"}


class InvalidLink(ValueError):
    def __init__(self, link: str):
        super().__init__(
            f"Couldn't find a YouTube video in {link!r}. Paste the video's link, e.g. https://www.youtube.com/watch?v=..."
        )


def video_id(link: str) -> str:
    """The video ID in a watch, short, live, shorts or embed link, or a bare ID. Other parameters are ignored."""
    link = link.strip()
    if _VIDEO_ID.match(link):
        return link
    url = urlparse(link if "//" in link else f"https://{link}")
    host = (url.hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        host = host.removeprefix(prefix)
    candidate = None
    if host == "youtu.be":
        candidate = url.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        query = parse_qs(url.query)
        if "v" in query:
            candidate = query["v"][0]
        else:
            parts = url.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in _PATH_FORMS:
                candidate = parts[1]
    if not candidate or not _VIDEO_ID.match(candidate):
        raise InvalidLink(link)
    return candidate


def watch_url(video_id: str, seconds: int | None = None) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"
    return url if seconds is None else f"{url}&t={seconds}s"


def clock(seconds: int) -> str:
    """A Timestamp as YouTube shows it: m:ss, or h:mm:ss from an hour."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
