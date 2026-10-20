"""One folder per Set, holding its saved Recognitions, Tracklists and (when kept) audio."""

import json
import os
import shutil
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from djset.comments import Comment
from djset.recognition import Clip, Recognition
from djset.tracklist import Mode, SetInfo, Tracklist

_COMMENTS = TypeAdapter(list[Comment])
_TRACKLIST_NAMES = {
    Mode.NORMAL: "tracklist",
    Mode.COMMENTS_ONLY: "tracklist-comments-only",
    Mode.AUDIO_ONLY: "tracklist-audio-only",
}


def default_root() -> Path:
    return Path(os.environ.get("DJSET_HOME", "~/djset")).expanduser()


class SetStore:
    def __init__(self, root: Path, video_id: str):
        self.folder = root / video_id

    def clear(self) -> None:
        """Forget everything saved for the Set except kept audio."""
        if not self.folder.exists():
            return
        for path in self.folder.iterdir():
            if path.name.startswith("audio."):
                continue
            shutil.rmtree(path) if path.is_dir() else path.unlink()

    def load_set_info(self) -> SetInfo | None:
        return self._load(self.folder / "set.json", SetInfo.model_validate_json)

    def save_set_info(self, info: SetInfo) -> None:
        self._write(self.folder / "set.json", info.model_dump_json(indent=2))

    def load_comments(self) -> list[Comment] | None:
        return self._load(self.folder / "comments.json", _COMMENTS.validate_json)

    def save_comments(self, comments: list[Comment]) -> None:
        self._write(self.folder / "comments.json", _COMMENTS.dump_json(comments).decode())

    def recognitions(self) -> dict[Clip, Recognition]:
        path = self.folder / "recognitions.jsonl"
        if not path.exists():
            return {}
        known = {}
        for line in path.read_text().splitlines():
            try:
                recognition = Recognition.model_validate_json(line)
            except ValidationError:
                continue  # a line cut short when a run was stopped mid-write
            known[recognition.clip] = recognition
        return known

    def add_recognition(self, recognition: Recognition) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / "recognitions.jsonl").open("a") as file:
            file.write(recognition.model_dump_json() + "\n")

    def tracklist_path(self, mode: Mode, suffix: str) -> Path:
        return self.folder / f"{_TRACKLIST_NAMES[mode]}{suffix}"

    def load_tracklist(self, mode: Mode) -> Tracklist | None:
        return self._load(self.tracklist_path(mode, ".json"), Tracklist.model_validate_json)

    def save_tracklist(self, tracklist: Tracklist, markdown: str) -> Path:
        self._write(self.tracklist_path(tracklist.mode, ".json"), tracklist.model_dump_json(indent=2))
        markdown_path = self.tracklist_path(tracklist.mode, ".md")
        self._write(markdown_path, markdown)
        return markdown_path

    def audio_path(self) -> Path | None:
        """The kept audio file, if a previous run kept one."""
        if not self.folder.exists():
            return None
        return next((p for p in sorted(self.folder.glob("audio.*")) if not p.name.endswith(".part")), None)

    def delete_audio(self) -> None:
        if self.folder.exists():
            for path in self.folder.glob("audio.*"):
                path.unlink()

    def _load(self, path: Path, parse):
        if not path.exists():
            return None
        try:
            return parse(path.read_text())
        except (ValidationError, json.JSONDecodeError):
            return None

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text)
        temporary.replace(path)
