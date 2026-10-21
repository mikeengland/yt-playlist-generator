"""The edges of a run: links in, model and tool setup, and the listener's answer about the Playlist."""

import pytest

from djset.cli import leave_out
from djset.evaluation import references
from djset.identify import MissingTool
from djset.links import InvalidLink, video_id
from djset.model_choice import DEFAULT_MODEL, ModelUnavailable, chosen_model, load_model
from djset.playlist import Suggestion
from djset.youtube import YouTube
from tests.fakes import spotify_track


@pytest.mark.parametrize(
    "link",
    [
        "https://www.youtube.com/watch?v=XNEO0Uq8zNA",
        "https://www.youtube.com/watch?v=XNEO0Uq8zNA&list=RDXNEO0Uq8zNA&start_radio=1&t=1739s&pp=ygUIc2lhbmd5b2-gBwE%3D",
        "https://youtu.be/XNEO0Uq8zNA?si=abc&t=30",
        "youtube.com/watch?v=XNEO0Uq8zNA",
        "https://m.youtube.com/watch?v=XNEO0Uq8zNA",
        "https://music.youtube.com/watch?v=XNEO0Uq8zNA&feature=share",
        "https://www.youtube.com/live/XNEO0Uq8zNA?feature=share",
        "https://www.youtube.com/embed/XNEO0Uq8zNA",
        "  XNEO0Uq8zNA  ",
    ],
)
def test_any_form_of_youtube_link_gives_the_video_id(link):
    assert video_id(link) == "XNEO0Uq8zNA"


@pytest.mark.parametrize("link", ["https://vimeo.com/123", "https://www.youtube.com/@Siangyoo", "not a link", ""])
def test_links_without_a_video_are_refused(link):
    with pytest.raises(InvalidLink):
        video_id(link)


def test_the_model_comes_from_the_option_then_the_setting_then_the_default():
    assert chosen_model("openai:gpt-5.2", {"DJSET_MODEL": "ollama:llama3.3"}) == "openai:gpt-5.2"
    assert chosen_model(None, {"DJSET_MODEL": "ollama:llama3.3"}) == "ollama:llama3.3"
    assert chosen_model(None, {}) == DEFAULT_MODEL == "anthropic:claude-sonnet-5-5"


def test_a_missing_api_key_says_which_setting_to_add(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(
        ModelUnavailable, match="needs the ANTHROPIC_API_KEY setting. Add `export ANTHROPIC_API_KEY=...`"
    ):
        load_model("anthropic:claude-sonnet-5-5")


def test_an_unknown_model_explains_the_format():
    with pytest.raises(ModelUnavailable, match="Models are written provider:model"):
        load_model("claude-sonnet-5-5")


def test_missing_tools_say_how_to_install_them(monkeypatch):
    monkeypatch.setenv("PATH", "")

    with pytest.raises(MissingTool, match="brew install deno ffmpeg"):
        YouTube().check(needs_audio=True)
    with pytest.raises(MissingTool, match="brew install deno$"):
        YouTube().check(needs_audio=False)


def test_both_reference_sets_are_stored():
    stored = {r.video_id: len([e for e in r.entries if e.named]) for r in references()}
    assert stored == {"XNEO0Uq8zNA": 36, "LTFCuhofnN8": 37}


SUGGESTIONS = [
    Suggestion(number=n, match=spotify_track("Artist", f"Track {n}").model_dump() | {"original": False}, plays=[])
    for n in range(1, 11)
]


@pytest.mark.parametrize(
    "answer, kept",
    [
        ("", list(range(1, 11))),
        ("  y ", list(range(1, 11))),
        ("3", [1, 2, 4, 5, 6, 7, 8, 9, 10]),
        ("3 7-9", [1, 2, 4, 5, 6, 10]),
        ("1,2, 10", [3, 4, 5, 6, 7, 8, 9]),
        ("7 - 9", [1, 2, 3, 4, 5, 6, 10]),
        ("1-10", []),
        ("n", []),
    ],
)
def test_the_listener_leaves_plays_out_by_number_and_range(answer, kept):
    assert [s.number for s in leave_out(SUGGESTIONS, answer)] == kept


@pytest.mark.parametrize("answer, problem", [("3 x", "isn't a number"), ("0", "within 1-10"), ("9-12", "within 1-10")])
def test_an_answer_that_isnt_understood_is_explained(answer, problem):
    with pytest.raises(ValueError, match=problem):
        leave_out(SUGGESTIONS, answer)
