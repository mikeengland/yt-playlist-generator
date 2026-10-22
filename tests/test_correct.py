"""Whole Corrections through the correction use case, with a scripted model."""

import asyncio
from pathlib import Path

import pytest

from djset.correct import Change, Corrected, CorrectionBoundaries, CorrectionFailed, correct
from djset.tracklist import Evidence, Mode, Play, Tracklist
from tests.fakes import ScriptedModel, output, read_json, set_info, track

QUANTUM_NOKIA = track("Quantum Roots", "NOKIA")
DRAKE_NOKIA = track("Drake", "NOKIA")
YEAH = track("Usher", "Yeah!")

PLAYS = [
    Play(timestamp=15, track=track("Spice Girls", "Wannabe"), evidence=Evidence.CONFIRMED),
    Play(timestamp=75, track=track("Fetty Wap", "Again"), evidence=Evidence.AUDIO_ONLY),
    Play(timestamp=160, track=QUANTUM_NOKIA, evidence=Evidence.AUDIO_ONLY, notes=["A comment says Drake – NOKIA"]),
    Play(timestamp=210, track=None, evidence=None),
    Play(timestamp=240, track=track("Usher", "U Remind Me"), evidence=Evidence.AUDIO_ONLY, blend=1),
    Play(timestamp=240, track=track("Teedra Moses", "Be Your Girl"), evidence=Evidence.AUDIO_ONLY, blend=1),
]
TRACKLIST = Tracklist(set=set_info(), mode=Mode.NORMAL, plays=PLAYS)


def run(correction: str, model: ScriptedModel, tmp_path: Path, tracklist: Tracklist = TRACKLIST) -> Corrected:
    return asyncio.run(correct(tracklist, correction, CorrectionBoundaries(model=model, store_root=tmp_path)))


def changes(*changes: dict) -> ScriptedModel:
    return ScriptedModel(output(changes=list(changes)))


def name(play: int, artist: str | None, title: str | None = None) -> dict:
    return {"change": "name", "play": play, "track": {"artist": artist, "title": title} if artist else None}


def summary(tracklist: Tracklist) -> list[tuple]:
    return [(p.timestamp, str(p.track) if p.track else None, p.evidence, p.blend) for p in tracklist.plays]


def test_naming_a_play_saves_the_track_the_listener_named(tmp_path):
    corrected = run("2:40 is drake - nokia", changes(name(3, "Drake", "NOKIA")), tmp_path)

    named = Play(timestamp=160, track=DRAKE_NOKIA, evidence=Evidence.LISTENER, notes=["Was Quantum Roots – NOKIA"])
    assert corrected.tracklist.plays == [*PLAYS[:2], named, *PLAYS[3:]]
    assert corrected.changes == [Change(PLAYS[2], named)]
    folder = tmp_path / "abcdefghijk"
    saved = read_json(folder / "tracklist.json")
    assert saved["plays"][2]["track"] == {"artist": "Drake", "title": "NOKIA", "version": None}
    assert saved["plays"][2]["evidence"] == "listener"
    markdown = (folder / "tracklist.md").read_text()
    assert (
        "| [2:40](https://www.youtube.com/watch?v=abcdefghijk&t=160s) | Drake – NOKIA | listener "
        "| Was Quantum Roots – NOKIA |" in markdown
    )
    assert "6 Plays: 1 Confirmed, 3 audio only, 1 listener, 1 Unknown Track" in markdown


def test_the_model_is_shown_only_the_numbered_tracklist_and_the_correction(tmp_path):
    model = changes(name(3, "Drake", "NOKIA"))

    run("2:40 is drake - nokia", model, tmp_path)

    assert model.prompts == [
        "Set: Test Set 001 (5:00, 300 seconds long)\n"
        "Tracklist:\n"
        "1. 0:15 (15s)  Spice Girls – Wannabe  [confirmed]\n"
        "2. 1:15 (75s)  Fetty Wap – Again  [audio only]\n"
        "3. 2:40 (160s)  Quantum Roots – NOKIA  [audio only; A comment says Drake – NOKIA]\n"
        "4. 3:30 (210s)  Unknown Track\n"
        "5. 4:00 (240s)  Usher – U Remind Me  [audio only; Blend 1]\n"
        "6. 4:00 (240s)  Teedra Moses – Be Your Girl  [audio only; Blend 1]\n"
        "\n"
        "The listener's Correction: 2:40 is drake - nokia"
    ]
    assert model.offered == []


def test_naming_an_unknown_track_or_making_a_play_one(tmp_path):
    corrected = run(
        "3:30 is Usher's Yeah! and 0:15 isn't Wannabe",
        changes(name(4, "Usher", "Yeah!"), name(1, None)),
        tmp_path,
    )

    assert corrected.tracklist.plays[0] == Play(
        timestamp=15, track=None, evidence=None, notes=["Was Spice Girls – Wannabe"]
    )
    assert corrected.tracklist.plays[3] == Play(timestamp=210, track=YEAH, evidence=Evidence.LISTENER)


def test_naming_the_same_track_marks_it_as_the_listeners_and_keeps_its_notes(tmp_path):
    corrected = run("2:40 is right", changes(name(3, "Quantum Roots", "NOKIA")), tmp_path)

    assert corrected.tracklist.plays[2] == PLAYS[2].model_copy(update={"evidence": Evidence.LISTENER})


def test_an_added_play_takes_its_place_in_order(tmp_path):
    corrected = run(
        "Usher – Yeah! comes in at 1:40",
        changes({"change": "add", "timestamp": 100, "track": {"artist": "Usher", "title": "Yeah!"}}),
        tmp_path,
    )

    added = Play(timestamp=100, track=YEAH, evidence=Evidence.LISTENER)
    assert corrected.tracklist.plays == [*PLAYS[:2], added, *PLAYS[2:]]
    assert corrected.changes == [Change(None, added)]


def test_a_play_added_to_a_blend_shares_its_timestamp_and_blends_are_numbered_in_order(tmp_path):
    corrected = run(
        "Usher – Yeah! is mixed over the 2:40 Track from 2:50",
        changes({"change": "add", "timestamp": 170, "track": {"artist": "Usher", "title": "Yeah!"}, "blend_with": 3}),
        tmp_path,
    )

    assert summary(corrected.tracklist)[2:] == [
        (160, "Quantum Roots – NOKIA", Evidence.AUDIO_ONLY, 1),
        (160, "Usher – Yeah!", Evidence.LISTENER, 1),
        (210, None, None, None),
        (240, "Usher – U Remind Me", Evidence.AUDIO_ONLY, 2),
        (240, "Teedra Moses – Be Your Girl", Evidence.AUDIO_ONLY, 2),
    ]


def test_removing_a_play_of_a_two_play_blend_leaves_no_blend(tmp_path):
    corrected = run("U Remind Me isn't in the Set", changes({"change": "remove", "play": 5}), tmp_path)

    assert summary(corrected.tracklist)[3:] == [
        (210, None, None, None),
        (240, "Teedra Moses – Be Your Girl", Evidence.AUDIO_ONLY, None),
    ]
    assert corrected.changes == [Change(PLAYS[4], None)]


def test_moving_a_play_moves_the_rest_of_its_blend(tmp_path):
    corrected = run(
        "the blend at 4:00 starts at 3:50, and Again at 1:05",
        changes({"change": "move", "play": 5, "timestamp": 230}, {"change": "move", "play": 2, "timestamp": 65}),
        tmp_path,
    )

    assert [play.timestamp for play in corrected.tracklist.plays] == [15, 65, 160, 210, 230, 230]
    assert [(c.before.timestamp, c.after.timestamp) for c in corrected.changes] == [(240, 230), (240, 230), (75, 65)]


def test_changes_refer_to_plays_as_listed_before_any_change(tmp_path):
    corrected = run(
        "drop Wannabe, and 2:40 is Drake",
        changes({"change": "remove", "play": 1}, name(3, "Drake", "NOKIA")),
        tmp_path,
    )

    assert [str(play.track) for play in corrected.tracklist.plays[:2]] == ["Fetty Wap – Again", "Drake – NOKIA"]


def test_an_impossible_change_is_sent_back_to_the_model(tmp_path):
    model = ScriptedModel(
        output(changes=[name(9, "Drake", "NOKIA")]),
        output(changes=[{"change": "add", "timestamp": 400, "track": None}]),
        output(changes=[{"change": "remove", "play": 1}, name(1, "Drake", "NOKIA")]),
        output(changes=[name(3, "Drake", "NOKIA")]),
    )

    corrected = run("2:40 is drake - nokia", model, tmp_path)

    assert model.retries == [
        "There is no Play 9: the Plays are numbered 1 to 6.\n\nFix the errors and try again.",
        "400 is past the end of the Set, at 300 seconds.\n\nFix the errors and try again.",
        "Play 1 is removed, so it can't also be changed.\n\nFix the errors and try again.",
    ]
    assert corrected.tracklist.plays[2].track == DRAKE_NOKIA


def test_a_correction_the_model_cant_make_changes_nothing_and_says_why(tmp_path):
    model = ScriptedModel(output(changes=[], reply="There's no Play near 9:00."))

    corrected = run("9:00 is Drake", model, tmp_path)

    assert corrected.tracklist == TRACKLIST
    assert corrected.changes == []
    assert corrected.reply == "There's no Play near 9:00."
    assert not (tmp_path / "abcdefghijk").exists()


def test_a_model_that_keeps_proposing_impossible_changes_leaves_the_tracklist_unchanged(tmp_path):
    model = ScriptedModel(*[output(changes=[name(9, "Drake", "NOKIA")])] * 4)

    with pytest.raises(CorrectionFailed, match="The Tracklist is unchanged"):
        run("2:40 is drake - nokia", model, tmp_path)

    assert not (tmp_path / "abcdefghijk").exists()
