"""YouTube comments, and the filter that keeps only those that can hold Comment Hints."""

import re

from pydantic import BaseModel

ROOT = "root"
REPLIES_KEPT_AFTER_QUESTION = 10
TOP_LEVEL_KEPT = 600

TIMESTAMP = re.compile(r"(?<![\d:])(?:\d{1,2}:)?\d{1,2}:[0-5]\d(?![\d:])")
TRACK_QUESTION = re.compile(
    r"""
    \b(?:what|what'?s|whats|wat|which|wht)\b[^.!\n]{0,40}\b(?:song|track|tune|name|called)\b
    | \b(?:song|track|tune)\s*(?:name|id|title)\b
    | \bname\s+of\s+(?:the|this|that)\b
    | \b(?:anyone|anybody|someone|somebody)\s+(?:know|knows|got|have)\b
    | \bwho\s+(?:sings|sang|is\s+singing|made)\b
    | \bshazam
    | \bid\s*\?
    | \btrack\s*list
    """,
    re.IGNORECASE | re.VERBOSE,
)


class Comment(BaseModel):
    id: str
    parent: str = ROOT
    author: str
    text: str
    likes: int = 0
    pinned: bool = False
    by_uploader: bool = False

    @property
    def is_reply(self) -> bool:
        return self.parent != ROOT


def has_timestamp(text: str) -> bool:
    return TIMESTAMP.search(text) is not None


def asks_for_track(text: str) -> bool:
    return TRACK_QUESTION.search(text) is not None


def filter_comments(comments: list[Comment]) -> list[Comment]:
    """Keep top-level comments with a timestamp, pinned or uploader comments, and the answers to track-ID questions.

    Keeps YouTube's order, with each kept reply after its top-level comment.
    """
    replies: dict[str, list[Comment]] = {}
    for comment in comments:
        if comment.is_reply:
            replies.setdefault(comment.parent, []).append(comment)

    threads: list[tuple[Comment, list[Comment]]] = []
    for comment in comments:
        if comment.is_reply:
            continue
        kept_replies = _kept_replies(comment, replies.get(comment.id, []))
        if kept_replies or comment.pinned or comment.by_uploader or has_timestamp(comment.text):
            threads.append((comment, kept_replies))

    if len(threads) > TOP_LEVEL_KEPT:
        most_liked = sorted(range(len(threads)), key=lambda i: threads[i][0].likes, reverse=True)
        threads = [threads[i] for i in sorted(most_liked[:TOP_LEVEL_KEPT])]

    return [comment for top, kept in threads for comment in (top, *kept)]


def _kept_replies(top: Comment, replies: list[Comment]) -> list[Comment]:
    """Replies with a timestamp, plus the replies that follow a track-ID question in the thread."""
    question_at = 0 if asks_for_track(top.text) else None
    kept = []
    for index, reply in enumerate(replies):
        if question_at is None and asks_for_track(reply.text):
            question_at = index
            kept.append(reply)
        elif has_timestamp(reply.text) or (
            question_at is not None and index - question_at <= REPLIES_KEPT_AFTER_QUESTION
        ):
            kept.append(reply)
    return kept


def comments_for_agent(description: str, comments: list[Comment]) -> str:
    """The description and filtered comments as compact text for the agent."""
    lines = ["Description:", description.strip() or "(empty)", "", f"Comments ({len(comments)} kept):"]
    for comment in comments:
        flags = [f"{comment.likes} likes"]
        if comment.pinned:
            flags.append("pinned")
        if comment.by_uploader:
            flags.append("by the uploader")
        prefix = "    reply " if comment.is_reply else ""
        text = comment.text.strip().replace("\n", "\n    " if comment.is_reply else "\n  ")
        lines.append(f"{prefix}[{comment.author}, {', '.join(flags)}] {text}")
    return "\n".join(lines)
