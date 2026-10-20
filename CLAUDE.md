# djset

A command-line agent that works out which Tracks a DJ plays in a YouTube Set, and when, and prints a Tracklist with links to each Timestamp. Spotify playlists come later.

## Language

`GLOSSARY.md` defines the domain terms (Set, Track, Play, Clip, Recognition, Comment Hint, ...). Read it before naming anything in code, docs or conversation, and use its terms over the ones it lists under _Avoid_.

## Decisions

`docs/adr/` records decisions that are hard to reverse. Read the relevant ADR before changing how Plays are identified or how the agent is built.

## Stack

- **Python 3.12**: the released `shazamio` (0.8.1) is reported to break on 3.13+.
- **uv** for environments and dependencies.
- **Pydantic AI** for the agent. The default model is Claude Opus 5.5 (`anthropic:claude-opus-5-5`); the model is chosen by a single setting so other providers (OpenAI, Ollama, ...) plug in without changes to the agent code.
- **yt-dlp** for YouTube metadata, comments and audio. It needs **Deno** installed to solve YouTube's player challenges.
- **shazamio** for recognition, behind a single swap point so a licensed service can replace or join it.
- **Typer** for the command line.
- **ffmpeg** (Homebrew on macOS) for cutting Clips.

## Environment

The tool runs on the user's Mac. Cloud sessions can fetch YouTube metadata and comments, but YouTube bot-checks downloads and the network policy blocks its media hosts (`*.googlevideo.com`), so Set audio can only be downloaded locally.
