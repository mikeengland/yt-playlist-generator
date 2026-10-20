"""The edges of a run: links in, model and tool setup, and evaluation scoring."""

import pytest

from djset.evaluation import Reference, ReferenceEntry, references, score
from djset.identify import MissingTool
from djset.links import InvalidLink, video_id
from djset.model_choice import DEFAULT_MODEL, ModelUnavailable, chosen_model, load_model
from djset.tracklist import Evidence, Mode, Play, Tracklist
from djset.youtube import YouTube
from tests.fakes import set_info, track


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


def tracklist(*plays: Play) -> Tracklist:
    return Tracklist(set=set_info(), mode=Mode.NORMAL, plays=list(plays))


def found(timestamp: int, artist: str, title: str, version: str | None = None) -> Play:
    return Play(timestamp=timestamp, track=track(artist, title, version), evidence=Evidence.AUDIO_ONLY)


def test_scoring_matches_artist_and_title_within_45_seconds_ignoring_versions():
    result = score(
        REFERENCE,
        tracklist(
            found(30, "Spice Girls", "Wannabe (Radio Edit)"),
            found(75, "Kanye West, Lil Pump", "I Love It (feat. Adele Givens)"),
            found(75, "Rihanna", "BBHMM"),
            found(240, "Tems", "Love Me JeJe", version="FNKY Remix"),
        ),
    )

    assert (result.found, result.named, result.wrong) == (3, 4, 1)


def test_scoring_ignores_stretches_the_reference_does_not_name():
    result = score(
        REFERENCE,
        tracklist(
            found(10, "Spice Girls", "Wannabe"),
            found(130, "Someone", "Something Else"),
            found(200, "Drake", "NOKIA"),
            Play(timestamp=150, track=None, evidence=None),
        ),
    )

    assert (result.found, result.wrong, result.unknown, result.plays) == (1, 1, 1, 4)
