# djset

A command-line agent that works out which Tracks a DJ plays in a YouTube Set, and when, and prints a Tracklist with links to each Timestamp. With `--spotify` it then makes a private Spotify playlist of the Tracks the listener chooses.

## Language

`GLOSSARY.md` defines the domain terms (Set, Track, Play, Clip, Recognition, Comment Hint, ...). Read it before naming anything in code, docs or conversation, and use its terms over the ones it lists under _Avoid_.

## Decisions

`docs/adr/` records decisions that are hard to reverse. Read the relevant ADR before changing how Plays are identified or how the agent is built.

## Specs

`docs/specs/` holds the spec for each feature. Read the relevant spec before building or changing that feature.

## Stack

- **Python 3.12**: the released `shazamio` (0.8.1) is reported to break on 3.13+.
- **uv** for environments and dependencies.
- **Pydantic AI** for the agent. The default model is Claude Sonnet 5.5 (`anthropic:claude-sonnet-5-5`); the model is chosen by a single setting (`--model` or `DJSET_MODEL`) so other providers (OpenAI, Ollama, ...) plug in without changes to the agent code.
- **yt-dlp** for YouTube metadata, comments and audio. It needs **Deno** installed to solve YouTube's player challenges.
- **shazamio** for recognition, behind a single swap point so a licensed service can replace or join it.
- **Typer** for the command line.
- **ffmpeg** (Homebrew on macOS) for cutting Clips.
- **httpx2** (already installed with Pydantic AI) for Spotify's Web API, through our own small client rather than spotipy. Matching Tracks to Spotify is plain code: Spotify's Developer Policy forbids feeding Spotify content into an AI model, so it must never reach the agent.

## Environment

The tool runs on the user's Mac. Cloud sessions can fetch YouTube metadata and comments, but YouTube bot-checks downloads and the network policy blocks its media hosts (`*.googlevideo.com`), so Set audio can only be downloaded locally. Spotify's API is reachable, but signing in needs a browser on the same machine, so Playlists can only be made locally too.

## Commands

- `uv run pytest`: the tests. They run offline, with fakes for YouTube, recognition, the model and the clock.
- `uv run djset <youtube-link>`: identify a Set. Results go to `~/djset/<video-id>/` (or `$DJSET_HOME`).
- `uv run djset <youtube-link> --spotify`: then make its Spotify playlist. Needs `SPOTIFY_CLIENT_ID` from the user's own Development Mode app.
- `uv run djset <youtube-link> --correct "2:40 is Drake – Nokia"`: correct the Tracklist in the listener's own words. Needs the model's API key.
- `uv run djset-eval [--model provider:model]... [--runs N]`: compare models against the reference Sets in `src/djset/references/`. It uses the real services, so it costs time and money; run it only when asked.
- `uv run python scripts/capture_fixtures.py [--cloud] <video-id>...`: refresh test fixtures. In cloud sessions pass `--cloud`, and put a Deno binary on `PATH` (for example `uv pip install deno` into a scratch environment).

## Code

There are three use cases, each a seam tests drive (see each spec's Testing Decisions):

- `src/djset/identify.py` turns a YouTube link into a Tracklist. Its boundaries are the Set Source (`youtube.py`), the Recogniser (`recognition.py`), the model (`model_choice.py`) and the Clock (`clock.py`).
- `src/djset/correct.py` applies the listener's Correction to a Tracklist. Its boundary is the model, which only proposes changes to numbered Plays; plain code applies them. It must never be given Spotify data.
- `src/djset/playlist.py` turns a Tracklist into a Playlist. Its boundaries are Spotify (`spotify.py`), the Chooser (the listener's confirmation or a Correction; `TerminalChooser` in `cli.py`) and the Clock. Matching rules are in `matching.py`.

`tests/fakes.py` has a stand-in for each boundary. Tests assert on the resulting Tracklist or Playlist, saved files and what reaches a boundary, not on internals. The real Spotify client is tested against `httpx2.MockTransport` in `tests/test_spotify_adapter.py`.
