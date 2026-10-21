"""Whole identifications through the use case, with fake Set Source, Recogniser, model and Clock."""

import asyncio
from pathlib import Path

import pytest

from djset.comments import Comment
from djset.identify import Boundaries, Outcome, Request, identify
from djset.scanner import GaveUp
from djset.tracklist import Mode
from tests.fakes import (
    FakeClock,
    FixtureSource,
    ReplayRecogniser,
    ScriptedModel,
    Segment,
    TimelineRecogniser,
    call,
    finish,
    play,
    read_json,
    set_info,
    track,
)

LINK = "https://www.youtube.com/watch?v=abcdefghijk&list=RDabcdefghijk&start_radio=1&t=60s"
WANNABE = track("Spice Girls", "Wannabe")
AGAIN = track("Fetty Wap", "Again")
U_REMIND_ME = track("Usher", "U Remind Me")
NOKIA = track("Drake", "NOKIA")

# A five-minute Set: an intro, three Tracks, and a stretch recognition can't identify.
TIMELINE = [
    Segment(0, 10, None),
    Segment(10, 75, WANNABE),
    Segment(75, 150, AGAIN),
    Segment(150, 210, None),
    Segment(210, 300, U_REMIND_ME),
]


def run(request: Request, boundaries: Boundaries) -> Outcome:
    return asyncio.run(identify(request, boundaries))


def boundaries(
    tmp_path: Path, model: ScriptedModel, recogniser=None, source=None, clock=None, progress=None
) -> Boundaries:
    clock = clock or FakeClock()
    extra = {"progress": progress} if progress else {}
    return Boundaries(
        set_source=source or FixtureSource(set_info()),
        recogniser=recogniser or TimelineRecogniser(TIMELINE),
        model=model,
        clock=clock,
        store_root=tmp_path,
        **extra,
    )


def read_draft_then_finish(*plays) -> ScriptedModel:
    return ScriptedModel(call(("draft_tracklist", {}), ("comments", {})), finish(*plays))


class RecordingProgress:
    def __init__(self):
        self.pauses: list[float] = []
        self.notes: list[str] = []
        self.steps: list[str] = []

    def step(self, message):
        self.steps.append(message)

    def clips(self, done, total):
        pass

    def paused(self, wait, refusing_for, reason):
        self.pauses.append(wait)

    def note(self, message):
        self.notes.append(message)


def test_a_run_saves_the_tracklist_with_links_and_deletes_the_audio(tmp_path):
    model = read_draft_then_finish(
        play(15, "Spice Girls", "Wannabe", "confirmed"),
        play(75, "Fetty Wap", "Again", "audio only"),
        play(150, evidence=None),
        play(210, "Usher", "U Remind Me", "audio only", notes=["A comment says Usher – Yeah!"]),
    )

    outcome = run(Request(LINK), boundaries(tmp_path, model))

    folder = tmp_path / "abcdefghijk"
    saved = read_json(folder / "tracklist.json")
    assert saved["schema_version"] == 1
    assert saved["set"]["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert [p["timestamp"] for p in saved["plays"]] == [15, 75, 150, 210]
    assert saved["plays"][2]["track"] is None
    markdown = (folder / "tracklist.md").read_text()
    assert outcome.markdown_path == folder / "tracklist.md"
    assert (
        "| [0:15](https://www.youtube.com/watch?v=abcdefghijk&t=15s) | Spice Girls – Wannabe | Confirmed |  |"
        in markdown
    )
    assert "| [2:30](https://www.youtube.com/watch?v=abcdefghijk&t=150s) | Unknown Track |  |  |" in markdown
    assert "A comment says Usher – Yeah!" in markdown
    assert "4 Plays: 1 Confirmed, 2 audio only, 1 Unknown Track" in markdown
    assert not list(folder.glob("audio.*"))
    assert len((folder / "recognitions.jsonl").read_text().splitlines()) == 20


def test_keep_audio_keeps_the_download(tmp_path):
    run(Request(LINK, keep_audio=True), boundaries(tmp_path, read_draft_then_finish()))

    assert (tmp_path / "abcdefghijk" / "audio.m4a").exists()


def test_the_draft_merges_consecutive_recognitions_and_marks_gaps(tmp_path):
    model = read_draft_then_finish()

    run(Request(LINK), boundaries(tmp_path, model))

    draft = model.results["draft_tracklist"][0].splitlines()
    assert draft == [
        "Draft from 20 Clips. Times are when a Clip starts.",
        "0:00–0:12  nothing recognised",
        "0:15–1:00  Spice Girls – Wannabe  [4 Clips]",
        "1:15–2:15  Fetty Wap – Again  [5 Clips]",
        "2:30–3:27  nothing recognised",
        "3:30–4:45  Usher – U Remind Me  [6 Clips]",
    ]


def test_the_draft_flags_lone_recognitions_and_possible_blends(tmp_path):
    timeline = [Segment(10, 33, WANNABE), Segment(33, 39, NOKIA), Segment(39, 75, WANNABE), *TIMELINE[2:]]
    model = read_draft_then_finish()

    run(Request(LINK), boundaries(tmp_path, model, recogniser=TimelineRecogniser(timeline)))

    draft = model.results["draft_tracklist"][0]
    assert "0:15–1:00  Spice Girls – Wannabe  [3 Clips, overlaps Drake – NOKIA (possible Blend)]" in draft
    assert "0:30  Drake – NOKIA  [lone: heard by 1 Clip, overlaps Spice Girls – Wannabe (possible Blend)]" in draft


def test_a_track_played_again_later_is_a_second_play_in_the_draft(tmp_path):
    timeline = [Segment(0, 60, WANNABE), Segment(60, 240, AGAIN), Segment(240, 300, WANNABE)]
    model = read_draft_then_finish()

    run(Request(LINK), boundaries(tmp_path, model, recogniser=TimelineRecogniser(timeline)))

    draft = model.results["draft_tracklist"][0]
    assert "0:00–0:45  Spice Girls – Wannabe  [4 Clips]" in draft
    assert "4:00–4:45  Spice Girls – Wannabe  [4 Clips]" in draft


def test_blends_share_a_timestamp_on_adjacent_lines(tmp_path):
    model = read_draft_then_finish(
        play(80, "Fetty Wap", "Again", "audio only", blend=7),
        play(15, "Spice Girls", "Wannabe", "confirmed"),
        play(75, "Drake", "NOKIA", "comments only", blend=7),
        play(210, "Usher", "U Remind Me", "audio only", blend=3),
    )

    outcome = run(Request(LINK), boundaries(tmp_path, model))

    plays = [(p.timestamp, str(p.track), p.blend) for p in outcome.tracklist.plays]
    assert plays == [
        (15, "Spice Girls – Wannabe", None),
        (75, "Fetty Wap – Again", 1),
        (75, "Drake – NOKIA", 1),
        (210, "Usher – U Remind Me", None),
    ]
    markdown = outcome.markdown_path.read_text()
    assert (
        "| [1:15](https://www.youtube.com/watch?v=abcdefghijk&t=75s) | Drake – NOKIA | comments only | Blend |"
        in markdown
    )


def test_an_edit_named_by_a_comment_is_kept_as_the_version(tmp_path):
    model = read_draft_then_finish(
        play(75, "Tems", "Love Me JeJe", "confirmed", version="FNKY Remix", notes=["Recognition heard the Original"]),
    )

    outcome = run(Request(LINK), boundaries(tmp_path, model))

    assert str(outcome.tracklist.plays[0].track) == "Tems – Love Me JeJe (FNKY Remix)"
    assert (
        "Tems – Love Me JeJe (FNKY Remix) | Confirmed | Recognition heard the Original"
        in outcome.markdown_path.read_text()
    )


def test_listener_evidence_from_the_agent_is_sent_back(tmp_path):
    model = ScriptedModel(
        call(("draft_tracklist", {}), ("comments", {})),
        finish(play(15, "Spice Girls", "Wannabe", "listener")),
        finish(play(15, "Spice Girls", "Wannabe", "audio only")),
    )

    outcome = run(Request(LINK), boundaries(tmp_path, model))

    assert model.retries == [
        '"listener" evidence is only for the listener\'s Corrections; nobody has corrected this Set.'
        "\n\nFix the errors and try again."
    ]
    assert outcome.tracklist.plays[0].evidence == "audio only"


def test_the_agent_sees_only_comments_that_can_hold_comment_hints(tmp_path):
    source = FixtureSource(
        set_info(description="Recorded live in Perth."),
        [
            Comment(id="a", author="@fan", text="this set is fire 🔥", likes=900),
            Comment(id="b", author="@lister", text="7:50 numb\n8:50 24k magic", likes=95),
            Comment(id="c", author="@asker", text="what's the song with the girl's voice??", likes=3),
            Comment(id="c.1", parent="c", author="@helper", text="Cry Me A River - Justin Timberlake"),
            Comment(id="d", author="@chatter", text="who else is here in 2026"),
            Comment(id="d.1", parent="d", author="@another", text="me"),
        ],
    )
    model = read_draft_then_finish()

    run(Request(LINK), boundaries(tmp_path, model, source=source))

    comments = model.results["comments"][0]
    assert "Recorded live in Perth." in comments
    assert "7:50 numb" in comments
    assert "what's the song with the girl's voice??" in comments
    assert "Cry Me A River - Justin Timberlake" in comments
    assert "this set is fire" not in comments
    assert "who else is here" not in comments
    assert "\nme" not in comments


def test_the_comment_filter_keeps_about_a_sixth_of_the_design_sets_comments(tmp_path):
    source = FixtureSource.from_fixture("yA9girCNcds")
    model = ScriptedModel(call(("comments", {})), finish())

    run(Request("https://youtu.be/yA9girCNcds", mode=Mode.COMMENTS_ONLY), boundaries(tmp_path, model, source=source))

    comments = model.results["comments"][0]
    top_level = [line for line in comments.splitlines() if line.startswith("[@")]
    assert 150 <= len(top_level) <= 300
    assert "7:50 numb" in comments


def test_comments_only_mode_uses_no_audio(tmp_path):
    source = FixtureSource(
        set_info(), [Comment(id="a", author="@lister", text="0:15 Wannabe - Spice Girls\n1:15 Again")]
    )
    model = ScriptedModel(call(("comments", {})), finish(play(15, "Spice Girls", "Wannabe", "confirmed")))

    outcome = run(Request(LINK, mode=Mode.COMMENTS_ONLY), boundaries(tmp_path, model, source=source))

    assert model.offered == ["set_info", "comments"]
    assert "audio" not in source.calls
    assert outcome.tracklist.plays[0].evidence == "comments only"
    assert (tmp_path / "abcdefghijk" / "tracklist-comments-only.md").exists()


def test_audio_only_mode_hides_the_comments(tmp_path):
    source = FixtureSource(set_info(), [Comment(id="a", author="@lister", text="0:15 Wannabe - Spice Girls")])
    model = ScriptedModel(call(("draft_tracklist", {})), finish(play(15, "Spice Girls", "Wannabe", "confirmed")))

    outcome = run(Request(LINK, mode=Mode.AUDIO_ONLY), boundaries(tmp_path, model, source=source))

    assert model.offered == ["set_info", "draft_tracklist", "recognise"]
    assert "comments" not in source.calls
    assert outcome.tracklist.plays[0].evidence == "audio only"


def test_a_finished_set_is_shown_again_without_any_work(tmp_path):
    source = FixtureSource(set_info())
    run(
        Request(LINK),
        boundaries(tmp_path, read_draft_then_finish(play(15, "Spice Girls", "Wannabe", "audio only")), source=source),
    )
    calls_after_first_run = list(source.calls)
    recogniser = TimelineRecogniser(TIMELINE)

    again = run(Request("abcdefghijk"), boundaries(tmp_path, ScriptedModel(), recogniser=recogniser, source=source))

    assert again.reused
    assert str(again.tracklist.plays[0].track) == "Spice Girls – Wannabe"
    assert source.calls == calls_after_first_run
    assert recogniser.requests == []


def test_fresh_ignores_saved_results(tmp_path):
    run(Request(LINK), boundaries(tmp_path, read_draft_then_finish(play(15, "Spice Girls", "Wannabe", "audio only"))))
    recogniser = TimelineRecogniser(TIMELINE)

    again = run(
        Request(LINK, fresh=True),
        boundaries(
            tmp_path, read_draft_then_finish(play(75, "Fetty Wap", "Again", "audio only")), recogniser=recogniser
        ),
    )

    assert not again.reused
    assert str(again.tracklist.plays[0].track) == "Fetty Wap – Again"
    assert len(recogniser.requests) == 20


def test_refusals_pause_with_growing_waits_then_carry_on_more_slowly(tmp_path):
    clock = FakeClock()
    recogniser = TimelineRecogniser(TIMELINE, clock=clock, refuse_between=[(0, 600)])
    progress = RecordingProgress()

    outcome = run(
        Request(LINK), boundaries(tmp_path, read_draft_then_finish(), recogniser, clock=clock, progress=progress)
    )

    assert not outcome.reused
    assert progress.pauses == [30, 60, 120, 240, 480]
    assert len(recogniser.requests) == 20
    assert clock.sleeps[5:] == [2.0] * 19


def test_continuous_refusals_give_up_after_about_half_an_hour_and_the_next_run_resumes(tmp_path):
    clock = FakeClock()
    refusing = TimelineRecogniser(TIMELINE, clock=clock, refuse_after=8)

    with pytest.raises(GaveUp, match="run the same command later"):
        run(Request(LINK), boundaries(tmp_path, read_draft_then_finish(), refusing, clock=clock))

    assert 30 * 60 <= sum(clock.sleeps) <= 40 * 60
    healthy = TimelineRecogniser(TIMELINE)
    run(Request(LINK), boundaries(tmp_path, read_draft_then_finish(), healthy))
    assert len(healthy.requests) == 20 - 8
    assert not set(healthy.requests) & set(refusing.requests)


def test_follow_up_clips_are_capped(tmp_path):
    model = ScriptedModel(
        call(("recognise", {"start": 150, "end": 210, "every": 5, "clip_length": 8})),
        call(("recognise", {"start": 150, "end": 195, "every": 5, "clip_length": 8})),
        call(("recognise", {"start": 200, "end": 210, "every": 5, "clip_length": 8})),
        call(("recognise", {"start": 15, "end": 75, "every": 15, "clip_length": 12})),
        finish(),
    )
    recogniser = TimelineRecogniser(TIMELINE)

    run(Request(LINK), boundaries(tmp_path, model, recogniser=recogniser))

    too_many, allowed, none_left, already_saved = model.results["recognise"]
    assert too_many.startswith("Refused: that needs 12 new Clips but only 9 follow-up Clips are left.")
    assert allowed.startswith("0 follow-up Clips left. AI budget left: ")
    assert "2:30 (8s Clip): nothing recognised" in allowed
    assert none_left.startswith("No follow-up Clips are left.")
    assert "0:15 (12s Clip): Spice Girls – Wannabe" in already_saved
    assert len(recogniser.requests) == 20 + 9


def test_follow_up_recognitions_join_the_draft(tmp_path):
    timeline = [*TIMELINE[:3], Segment(150, 170, NOKIA), Segment(170, 210, None), TIMELINE[4]]
    model = ScriptedModel(
        call(("recognise", {"start": 140, "end": 165, "every": 5, "clip_length": 8})),
        call(("draft_tracklist", {})),
        finish(),
    )

    run(Request(LINK), boundaries(tmp_path, model, recogniser=TimelineRecogniser(timeline)))

    draft = model.results["draft_tracklist"][0]
    assert "2:30–2:40  Drake – NOKIA  [4 Clips]" in draft
    assert "2:45–3:27  nothing recognised" in draft


def test_when_the_ai_budget_runs_out_the_tracklist_comes_from_recognition(tmp_path):
    progress = RecordingProgress()
    model = ScriptedModel(*[call(("draft_tracklist", {}))] * 100)

    outcome = run(Request(LINK), boundaries(tmp_path, model, progress=progress))

    plays = [(p.timestamp, str(p.track) if p.track else None, p.evidence) for p in outcome.tracklist.plays]
    assert plays == [
        (15, "Spice Girls – Wannabe", "audio only"),
        (75, "Fetty Wap – Again", "audio only"),
        (150, None, None),
        (210, "Usher – U Remind Me", "audio only"),
    ]
    assert any("budget ran out" in note for note in progress.notes)


def test_saved_recognitions_from_a_real_run_can_be_replayed(tmp_path):
    first = tmp_path / "first"
    run(Request(LINK), boundaries(first, read_draft_then_finish(), recogniser=TimelineRecogniser(TIMELINE)))
    replay = ReplayRecogniser(first / "abcdefghijk" / "recognitions.jsonl")
    model = read_draft_then_finish()

    run(Request(LINK), boundaries(tmp_path / "second", model, recogniser=replay))

    assert "0:15–1:00  Spice Girls – Wannabe  [4 Clips]" in model.results["draft_tracklist"][0]


def test_each_run_sees_only_the_grid_and_its_own_follow_ups(tmp_path):
    follow_ups = ("recognise", {"start": 150, "end": 195, "every": 5, "clip_length": 8})
    first = run(Request(LINK), boundaries(tmp_path, ScriptedModel(call(follow_ups), finish())))
    recogniser = TimelineRecogniser(TIMELINE)
    model = ScriptedModel(call(("draft_tracklist", {})), call(follow_ups), finish())

    second = run(Request(LINK, rerun=True), boundaries(tmp_path, model, recogniser=recogniser))

    assert model.results["draft_tracklist"][0].startswith("Draft from 20 Clips.")
    assert model.results["recognise"][0].startswith("0 follow-up Clips left.")
    assert recogniser.requests == []
    assert first.stats.follow_up_clips == second.stats.follow_up_clips == 9
    assert (second.stats.requests, second.stats.cost) == (3, None)
