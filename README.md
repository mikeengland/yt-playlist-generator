# djset

Works out which Tracks a DJ plays in a YouTube Set, and when. It listens to the Set by sending short Clips of its audio to Shazam, reads the description and comments as hints, and uses an AI agent to settle gaps and disagreements. It prints a Tracklist where every Timestamp links to that moment in the video, and saves it as Markdown and JSON.

## Setup (macOS)

```sh
brew install uv ffmpeg deno
uv sync
export ANTHROPIC_API_KEY=...   # or another provider's key, see --model
```

## Use

```sh
uv run djset "https://www.youtube.com/watch?v=yA9girCNcds"
```

A one-hour Set takes roughly 15–20 minutes, mostly waiting on Shazam. Progress is shown as it goes, and everything recognised is saved as it arrives, so a stopped run carries on where it left off. Running a finished Set again shows its saved Tracklist instantly.

Each Play says what it is based on:

- **Confirmed**: recognition and a comment agree.
- **audio only**: recognition alone.
- **comments only**: a comment alone, for a stretch recognition couldn't identify.

Stretches nobody could identify are listed as **Unknown Track**, with their own link so you can listen.

Options:

| Option | What it does |
| --- | --- |
| `--comments-only` | Use only the description and comments: no audio, about a minute. |
| `--fresh` | Ignore saved results and identify the Set again. |
| `--keep-audio` | Keep the downloaded audio (normally deleted after a run). |
| `--cookies-from-browser chrome` | Use your browser's YouTube sign-in, for age-restricted Sets or sign-in checks. |
| `--model openai:gpt-5.2` | Use another AI model. Also settable as `DJSET_MODEL`. Default: `anthropic:claude-sonnet-5-5`. |

Results are saved in `~/djset/<video-id>/`: `tracklist.md`, `tracklist.json` (versioned, for the later Spotify step) and `recognitions.jsonl`.

## Development

```sh
uv run pytest     # offline, no API keys needed
uv run djset-eval # scores against reference Sets; uses Shazam and the AI model for real
```

See `CLAUDE.md`, `GLOSSARY.md`, `docs/adr/` and `docs/specs/`.
