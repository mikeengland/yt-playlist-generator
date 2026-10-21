"""The real Shazam and ffmpeg adapters, against a local HTTP server and generated audio (no network)."""

import asyncio
import math
import shutil
import struct
import wave
from pathlib import Path

import pytest
from aiohttp import web

from djset.audio import FfmpegAudio
from djset.recognition import Clip, Refused, ShazamRecogniser, _RefusingClient
from djset.tracklist import Track


def tone(path: Path, seconds: int, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        samples = (int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(seconds * rate))
        file.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return path


async def serve(status: int, body: str, content_type: str = "application/json"):
    async def handler(request):
        return web.Response(status=status, text=body, content_type=content_type)

    app = web.Application()
    app.router.add_post("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/"


def request(status: int, body: str, content_type: str = "application/json"):
    async def go():
        runner, url = await serve(status, body, content_type)
        try:
            return await _RefusingClient().request("POST", url, json={})
        finally:
            await runner.cleanup()

    return asyncio.run(go())


def test_shazam_answers_are_passed_through():
    assert request(200, '{"matches": []}') == {"matches": []}


@pytest.mark.parametrize(
    "status, body, content_type",
    [(429, "Too Many Requests", "text/plain"), (503, "{}", "application/json"), (200, "<html>", "text/html")],
)
def test_rate_limits_outages_and_odd_answers_are_refusals(status, body, content_type):
    with pytest.raises(Refused):
        request(status, body, content_type)


def test_an_unreachable_shazam_is_a_refusal():
    with pytest.raises(Refused, match="couldn't be reached"):
        asyncio.run(_RefusingClient().request("POST", "http://127.0.0.1:9/", json={}))


class AnsweringClient:
    def __init__(self, answer):
        self.answer = answer
        self.sent = []

    async def request(self, method, url, *args, **kwargs):
        self.sent.append(kwargs["json"])
        return self.answer


def recognise(answer, tmp_path: Path):
    recogniser = ShazamRecogniser(http_client=AnsweringClient(answer))
    return asyncio.run(recogniser.recognise(Clip(0, 12), tone(tmp_path / "clip.wav", 12)))


def test_a_shazam_match_becomes_a_track(tmp_path):
    answer = {"matches": [{"id": "1"}], "track": {"title": "Wannabe", "subtitle": "Spice Girls"}}

    assert recognise(answer, tmp_path) == Track(artist="Spice Girls", title="Wannabe")


def test_no_match_is_nothing_recognised(tmp_path):
    assert recognise({"matches": [], "retryms": 4000}, tmp_path) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_ffmpeg_cuts_a_mono_16khz_clip(tmp_path):
    source = tone(tmp_path / "set.wav", 30, rate=44100)

    FfmpegAudio(source).cut(Clip(10, 12), tmp_path / "clip.wav")

    with wave.open(str(tmp_path / "clip.wav")) as clip:
        assert (clip.getnchannels(), clip.getframerate()) == (1, 16000)
        assert clip.getnframes() == 12 * 16000
