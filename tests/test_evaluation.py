"""Scoring against reference tracklists, and comparing models through the use case with fakes."""

import asyncio
import json
from datetime import datetime

import pytest

from djset.evaluation import RECOGNITION_ALONE, Reference, ReferenceEntry, compare, save, score, summarise
from djset.identify import Boundaries
from djset.tracklist import Evidence, Mode, Play
from tests.fakes import FakeClock, FixtureSource, ScriptedModel, TimelineRecogniser, call, finish, play, set_info, track
from tests.test_identify import TIMELINE

REFERENCE = Reference(
    video_id="abcdefghijk",
    title="Test Set 001",
    source="a test",
    entries=[
        ReferenceEntry(timestamp=10, artist="Spice Girls", title="Wannabe"),
        ReferenceEntry(timestamp=60, artist="Lil Pump & Kanye West", title="I Love It"),
        ReferenceEntry(timestamp=60, artist="Rihanna", title="Bitch Better Have My Money", also=["BBHMM"]),
        ReferenceEntry(timestamp=120, artist=None, title=None),
        ReferenceEntry(timestamp=180, artist="Tems", title="Love Me JeJe"),
    ],
)


def found(timestamp: int, artist: str, title: str, version: str | None = None) -> Play:
    return Play(timestamp=timestamp, track=track(artist, title, version), evidence=Evidence.AUDIO_ONLY)


def test_scoring_matches_artist_and_title_within_45_seconds_ignoring_versions():
    result = score(
        REFERENCE,
        [
            found(30, "Spice Girls", "Wannabe (Radio Edit)"),
            found(75, "Kanye West, Lil Pump", "I Love It (feat. Adele Givens)"),
            found(75, "Rihanna", "BBHMM"),
            found(240, "Tems", "Love Me JeJe", version="FNKY Remix"),
        ],
    )

    assert (result.found, result.named, result.correct, result.wrong) == (3, 4, 3, 1)
    assert (result.recall, result.precision, result.f1) == (0.75, 0.75, 0.75)
    assert result.timestamp_errors == [20, 15, 15]


def test_scoring_ignores_stretches_the_reference_does_not_name():
    result = score(
        REFERENCE,
        [
            found(10, "Spice Girls", "Wannabe"),
            found(130, "Someone", "Something Else"),
            found(200, "Drake", "NOKIA"),
            Play(timestamp=150, track=None, evidence=None),
        ],
    )

    assert (result.found, result.correct, result.wrong, result.unknown, result.plays) == (1, 1, 1, 1, 4)
    assert result.precision == 0.5


# The fake Set's reference: recognition can hear three of its four Tracks.
TEST_SET = Reference(
    video_id="abcdefghijk",
    title="Test Set 001",
    source="a test",
    entries=[
        ReferenceEntry(timestamp=10, artist="Spice Girls", title="Wannabe"),
        ReferenceEntry(timestamp=75, artist="Fetty Wap", title="Again"),
        ReferenceEntry(timestamp=150, artist="Drake", title="NOKIA"),
        ReferenceEntry(timestamp=210, artist="Usher", title="U Remind Me"),
    ],
)

SCRIPTS = {
    "careful": lambda: ScriptedModel(
        call(("draft_tracklist", {})),
        call(("recognise", {"start": 150, "end": 195, "every": 5, "clip_length": 8})),
        finish(
            play(15, "Spice Girls", "Wannabe", "audio only"),
            play(75, "Fetty Wap", "Again", "audio only"),
            play(150),
            play(210, "Usher", "U Remind Me", "audio only"),
        ),
    ),
    "sloppy": lambda: ScriptedModel(
        call(("draft_tracklist", {})),
        finish(play(15, "Spice Girls", "Wannabe", "audio only"), play(100, "Drake", "Passionfruit", "audio only")),
    ),
}


def compare_fakes(tmp_path, runs: int):
    source = FixtureSource(set_info())
    recogniser = TimelineRecogniser(TIMELINE)

    def boundaries_for(name: str) -> Boundaries:
        return Boundaries(source, recogniser, model=SCRIPTS[name](), clock=FakeClock(), store_root=tmp_path)

    rows = asyncio.run(compare([TEST_SET], list(SCRIPTS), [Mode.AUDIO_ONLY], runs, boundaries_for))
    return rows, recogniser


def test_models_are_compared_on_the_same_evidence_with_a_recognition_alone_baseline(tmp_path):
    rows, recogniser = compare_fakes(tmp_path, runs=2)

    assert [(r.model, r.run) for r in rows] == [
        ("careful", 1),
        ("sloppy", 1),
        ("careful", 2),
        ("sloppy", 2),
        (RECOGNITION_ALONE, 1),
    ]
    assert len(recogniser.requests) == 20 + 9
    summaries = {s.model: s for s in summarise(rows)}
    careful, sloppy, alone = summaries["careful"], summaries["sloppy"], summaries[RECOGNITION_ALONE]
    assert (careful.recall.mean, careful.precision.mean, careful.runs) == (0.75, 1.0, 2)
    assert careful.f1.mean == pytest.approx(6 / 7)
    assert (sloppy.recall.mean, sloppy.precision.mean, sloppy.wrong_per_set) == (0.25, 0.5, 1.0)
    assert (careful.follow_up_clips_per_set, sloppy.follow_up_clips_per_set) == (9, 0)
    assert careful.median_timestamp_error == 0
    assert alone.recall.mean == 0.75
    assert not (tmp_path / "abcdefghijk" / "tracklist-audio-only.json").exists()
    assert not list((tmp_path / "abcdefghijk").glob("audio.*"))


def test_the_comparison_is_saved_as_a_report(tmp_path):
    rows, _ = compare_fakes(tmp_path / "store", runs=1)

    path = save(rows, [TEST_SET], datetime(2026, 10, 21, 9, 30), tmp_path / "report")

    report = path.read_text()
    assert report.startswith("# djset evaluation, 2026-10-21 09:30")
    assert "| careful | audio only | 86% | 75% | 100% | 0.0 | 1.0 | 0s | unknown |" in report
    assert "| sloppy | audio only | 33% | 25% | 50% | 1.0 | 0.0 | 5s |" in report
    assert "| recognition alone (no AI) | audio only | 86% | 75% | 100% | 0.0 | 1.0 | 0s | – | – | – | 1 |" in report
    runs = json.loads((tmp_path / "report" / "runs.json").read_text())
    assert [r["model"] for r in runs] == ["careful", "sloppy", RECOGNITION_ALONE]
    assert runs[0]["f1"] == pytest.approx(6 / 7)
    assert len(list((tmp_path / "report" / "tracklists").iterdir())) == 2
