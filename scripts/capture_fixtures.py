"""Save a Set's info and comments (and, if present, its saved Recognitions) as test fixtures.

    uv run python scripts/capture_fixtures.py XNEO0Uq8zNA LTFCuhofnN8 yA9girCNcds

Recognitions come from a real run on the Mac: run `djset <link>` first, and its recognitions.jsonl is copied.
Pass --cloud in a Claude Code cloud session, where only YouTube's web_safari client gets through.
"""

import shutil
import sys
from pathlib import Path

from pydantic import TypeAdapter

from djset.comments import Comment
from djset.store import SetStore, default_root
from djset.youtube import YouTube

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main(arguments: list[str]) -> None:
    cloud = "--cloud" in arguments
    video_ids = [a for a in arguments if not a.startswith("--")]
    extractor_args = {"youtube": {"player_client": ["web_safari"], "player_skip": ["webpage"]}} if cloud else None
    youtube = YouTube(extractor_args=extractor_args)
    for video_id in video_ids:
        folder = FIXTURES / video_id
        folder.mkdir(parents=True, exist_ok=True)
        info = youtube.set_info(video_id)
        (folder / "set.json").write_text(info.model_dump_json(indent=2, exclude={"url"}) + "\n")
        comments = youtube.comments(video_id)
        (folder / "comments.json").write_bytes(TypeAdapter(list[Comment]).dump_json(comments, indent=1) + b"\n")
        recognitions = SetStore(default_root(), video_id).folder / "recognitions.jsonl"
        if recognitions.exists():
            shutil.copy(recognitions, folder / "recognitions.jsonl")
        replies = sum(c.is_reply for c in comments)
        print(f"{video_id}: {info.title!r}, {len(comments) - replies} comments and {replies} replies")


if __name__ == "__main__":
    main(sys.argv[1:])
