# djset

Works out which Tracks a DJ plays in a YouTube Set, and when. It listens to the Set by sending short Clips of its audio to Shazam, reads the description and comments as hints, and uses an AI agent to settle gaps and disagreements. It prints a Tracklist where every Timestamp links to that moment in the video, and saves it as Markdown and JSON. It can then make a private Spotify playlist of the Tracks you choose.

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
- **listener**: you named it in a Correction (see below).

Stretches nobody could identify are listed as **Unknown Track**, with their own link so you can listen.

Options:

| Option | What it does |
| --- | --- |
| `--comments-only` | Use only the description and comments: no audio, about a minute. |
| `--fresh` | Ignore saved results and identify the Set again. |
| `--keep-audio` | Keep the downloaded audio (normally deleted after a run). |
| `--cookies-from-browser chrome` | Use your browser's YouTube sign-in, for age-restricted Sets or sign-in checks. |
| `--model openai:gpt-5.2` | Use another AI model. Also settable as `DJSET_MODEL`. Default: `anthropic:claude-sonnet-5-5`. |
| `--spotify` | Then make a private Spotify playlist of the Tracks you choose (see below). |
| `--yes` | With `--spotify`, add every Track found on Spotify without asking. |
| `--new-playlist` | With `--spotify`, make a new playlist even if this Set's is already made. |
| `--playlist-name "Friday mix"` | With `--spotify`, name the playlist (default: the video's title). A new name makes a new playlist. |
| `--correct "2:40 is Drake – Nokia"` | Correct the Tracklist in your own words (see below). Repeat for more. |

Results are saved in `~/djset/<video-id>/`: `tracklist.md`, `tracklist.json`, `recognitions.jsonl`, and with `--spotify`, `spotify-searches.jsonl` and `playlist.json`.

## Correcting a Tracklist

When recognition gets a Track wrong and you know the right one, say so in your own words:

```sh
uv run djset "https://www.youtube.com/watch?v=yA9girCNcds" --correct "2:40 is Drake – Nokia"
```

The AI model works out which Plays you mean and what to change. You can name a Track ("2:40 is by Drake"), add one it missed ("Usher – Yeah! comes in at 12:10, over the Track before"), remove one that isn't in the Set, make one an Unknown Track ("I don't think 5:00 is right"), or move a Timestamp. Only the Plays you mention change. Each change is printed, corrected Plays are marked **listener** with the Track they replaced in their notes, and the corrected Tracklist is saved. Repeat `--correct`, or put several changes in one.

With `--spotify` you can also type a Correction at the playlist prompt, as soon as you see a Track "not on Spotify". The proposal is then made again, with the corrected Track searched on Spotify. The model only ever sees the Tracklist and your words, never anything from Spotify.

`--fresh` identifies the Set again from scratch, so it forgets Corrections.

## Spotify playlists

```sh
uv run djset "https://www.youtube.com/watch?v=yA9girCNcds" --spotify
```

This uses the Set's saved Tracklist, or identifies the Set first. It then finds each Track on Spotify and shows the proposed playlist. Press Enter to add everything, type numbers to leave some out (`3 7-9`), type a Correction (see above), or `n` to cancel. The playlist is private, named after the video, with the YouTube link in its description. Matching Tracks to Spotify involves no AI model.

- Tracks come out in Set order. A Track played twice is added once, and both Tracks of a Blend are added.
- A Spotify Match must be by the same artist with the same title, so covers, karaoke, live, sped-up and remixed versions aren't picked for the Original.
- When the DJ played an edit that isn't on Spotify, its Original is used and marked as such.
- Tracks not on Spotify are listed; Unknown Tracks are left out.
- Running `--spotify` again shows the playlist already made. If you deleted it on Spotify, add `--new-playlist` to make another (or `--playlist-name "..."` to make one with a new name). This reuses the saved Tracklist and Spotify searches, so it's quick. `--fresh` also makes a new one, but identifies the Set again first.

One-time setup, since Spotify only lets personal tools in through your own app:

1. Open the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and create an app. Set the Redirect URI to `http://127.0.0.1:8888/callback` and tick Web API. The app's owner needs Spotify Premium.
2. Copy its Client ID into your shell profile: `export SPOTIFY_CLIENT_ID=...`
3. The first `--spotify` run opens your browser to sign in. The sign-in is saved in `~/djset/spotify-token.json` and lasts six months, after which you're asked again. To sign in with a different Spotify account from the app's owner, add that account under the app's User Management first.

## Comparing models

`djset-eval` runs the tool on Sets with trusted reference tracklists and compares models on the same Recognitions:

```sh
export ANTHROPIC_API_KEY=... OPENAI_API_KEY=...
uv run djset-eval \
  --model anthropic:claude-haiku-5-5 \
  --model anthropic:claude-sonnet-5-5 \
  --model anthropic:claude-opus-5-5 \
  --model openai:gpt-6.1-sol \
  --runs 3
```

For each model and mode it reports F1, the share of reference Tracks found, precision, wrong Plays, Unknown Tracks, Timestamp error, cost, tokens and time per Set, with the spread across runs. A `recognition alone (no AI)` row shows what the agent adds. Each comparison is saved to `~/djset/evaluations/<date-time>/` as `report.md`, `runs.json` and every run's Tracklist. `--mode audio-only`, `--mode normal` (the default is both) and `--mode comments-only` pick what runs; `--runs 1` gives a quick pass.

It uses Shazam and the AI models for real, so it takes time and costs money: the first pass recognises each Set (about 10 minutes each), then each run adds the agent's time and cost.

## Development

```sh
uv run pytest     # offline, no API keys needed
```

See `CLAUDE.md`, `GLOSSARY.md`, `docs/adr/` and `docs/specs/`.
