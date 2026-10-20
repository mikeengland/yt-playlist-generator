"""A Set's downloaded audio, cut into Clips with ffmpeg."""

import subprocess
from pathlib import Path
from typing import Protocol

from djset.recognition import Clip


class AudioError(RuntimeError):
    pass


class SetAudio(Protocol):
    def cut(self, clip: Clip, wav: Path) -> None:
        """Write the Clip's audio to a mono 16 kHz WAV file."""
        ...


class FfmpegAudio:
    def __init__(self, path: Path):
        self.path = path

    def cut(self, clip: Clip, wav: Path) -> None:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", str(clip.start), "-t", str(clip.length), "-i", str(self.path),
            "-vn", "-ac", "1", "-ar", "16000", "-y", str(wav),
        ]  # fmt: skip
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise AudioError(f"ffmpeg couldn't cut the Clip at {clip.start}s: {result.stderr.strip()}")
