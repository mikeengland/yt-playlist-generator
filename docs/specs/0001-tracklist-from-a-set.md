# Spec: Tracklist from a YouTube Set (djset v1)

## Problem Statement

I watch recorded DJ Sets on YouTube and keep hearing Tracks I want to know, but I can't tell what they are. Sometimes someone posts a timestamped tracklist in the comments, but often nobody does: the one-hour Siangyoo Perth Set I watched has about 1,300 comments, and the best of them names 6 Tracks out of an estimated 60+ Plays, while 48 other commenters are asking what a track is. Shazam on my phone only helps for the moment I'm listening, and the DJ changes Track every 30–60 seconds. When I do find Tracks I like, I want to add them to Spotify, which means finding each one again by hand.

## Solution

A command-line tool, `djset`, that I run on my Mac with a YouTube Set link. It listens to the whole Set by sending short Clips of its audio to a recognition service, reads the description and comments as hints, and uses an AI agent to resolve gaps and disagreements between the two. It prints a Tracklist where every Play has a link that jumps to its Timestamp in the video, shows what the identification is based on, and says plainly when a Play is an Unknown Track. The Tracklist is saved so it can be reopened instantly and reused by the later Spotify step, which is a separate spec.

## User Stories

### Running it

1. As a listener, I want to run `djset` with a YouTube Set link, so that I get the Set's Tracklist without reading through comments.
2. As a listener, I want any form of YouTube link to work (watch links, short links, links with playlist, radio or start-time parameters), so that I can paste whatever is in my browser.
3. As a listener, I want to see progress during a run (Clips recognised out of the total, and when it is pausing because the recognition service refused requests), so that I know a 15–20 minute run is still working.
4. As a listener, I want the run to send Clips as fast as the recognition service accepts them, so that I wait no longer than necessary.
5. As a listener, I want the run to pause and retry with growing waits when the recognition service refuses requests, and then carry on more slowly, so that rate limits don't kill the run.
6. As a listener, I want the run to stop with a clear message if refusals continue for about 30 minutes, so that it doesn't hang indefinitely.
7. As a listener, I want re-running a stopped Set to pick up where it stopped, so that no recognition work is repeated.
8. As a listener, I want re-running a finished Set to show its saved Tracklist instantly, so that I can look it up again for free.
9. As a listener, I want a `--fresh` option that ignores saved results, so that I can redo a Set after the tool improves.
10. As a listener, I want the downloaded audio deleted after a run, so that Sets don't fill my disk.
11. As a listener, I want a `--keep-audio` option, so that I can keep the audio when I want to re-run or debug a Set.
12. As a listener, I want a `--comments-only` option that builds a Tracklist from the description and comments alone in about a minute, so that I get a quick answer when a Set already has a good comment tracklist.
13. As a listener, I want a `--cookies-from-browser` option, off by default, that lets the tool use my browser's YouTube login, so that age-restricted Sets or YouTube sign-in checks don't block me.
14. As a listener, I want a clear message when the video is private, removed or members-only, so that I know the tool can't help with that Set.
15. As a listener, I want a clear message when a required tool (ffmpeg, Deno) or the chosen model provider's API key is missing, including how to fix it, so that first-time setup is straightforward.

### The Tracklist

16. As a listener, I want every Play listed in order with its Timestamp, so that I can follow the Set.
17. As a listener, I want each Play's Timestamp to be a link that opens the video at that moment, so that I can hear the Track straight away.
18. As a listener, I want each identified Play to show the Track's artist and title, so that I can find it elsewhere.
19. As a listener, I want each Play to show what it is based on (Confirmed, audio only, or comments only), so that I know how much to trust it.
20. As a listener, I want a Play to note any Conflicting Hint, so that I can listen and judge for myself.
21. As a listener, I want stretches nobody could identify shown as Unknown Track with their own Timestamp link, so that I can listen and identify them myself.
22. As a listener, I want Tracks heard together shown as a Blend (separate Plays sharing a Timestamp, on adjacent lines, marked as a Blend), so that each Track can be found and added to Spotify on its own.
23. As a listener, I want a Track the DJ plays twice to appear as two Plays, so that the Tracklist matches what I hear.
24. As a listener, I want a Play to name the specific edit or remix when a comment names it and recognition heard its Original, so that I find the version the DJ actually played.
25. As a listener, I want one-off Recognitions inside another Track's run left out unless they are backed up, so that blends don't produce Plays that never happened.
26. As a listener, I accept Timestamps accurate to about 15 seconds, so that runs stay fast and cheap.
27. As a listener, I want the Tracklist printed in the terminal at the end of a run, so that I see the result immediately.
28. As a listener, I want the Tracklist saved as a Markdown file in a folder for that Set, so that I can open it later or share it.
29. As a listener, I want the Tracklist also saved as structured data, so that the later Spotify step can use it without re-running anything.

### Identification

30. As a listener, I want the whole Set sampled with a Clip every 15 seconds, so that fast-changing Sets don't lose Plays between Clips.
31. As a listener, I want the agent to read the description and comments in whatever format people use ("Artist – Title" or "Title – Artist", emoji, `@username` credits, "A x B – Title1 x Title2" for Blends, lowercase partial titles like "7:50 numb"), so that useful hints aren't lost to formatting.
32. As a listener, I want answers in replies to "what's the track at 24:20?" comments used as Comment Hints, so that crowd-sourced answers count.
33. As a listener, I want the agent to take extra Clips in gaps and around conflicts (more often, longer, or shifted), so that more Plays are identified.
34. As a listener, I want the agent's extra work capped at about 100 extra Clips per hour of Set and a fixed AI token budget, so that a confusing Set can't run up time or cost.
35. As a listener, I want whatever is still unidentified at the cap marked Unknown Track, so that every run finishes with a complete Tracklist.
36. As a listener, I want recognition to win when it disagrees with a comment, with the disagreement noted, so that the Tracklist favours what was actually heard.
37. As a listener, I want a Comment Hint that falls in a gap recognition couldn't fill to appear as a comments-only Play, so that I still get the community's answer.

### Building and maintaining it

38. As the developer, I want the AI model chosen with a `--model` option or a `DJSET_MODEL` setting (Claude Sonnet 5.5 by default), so that I can try OpenAI, Ollama or another Claude model without code changes.
39. As the developer, I want the recognition service behind a single interface, so that a licensed service such as AudD can replace or join Shazam later.
40. As the developer, I want the tests to run without YouTube, a recognition service or a paid model, so that they are fast, free and work in cloud sessions.
41. As the developer, I want fixtures captured from real runs (Set info, comments and Recognitions), so that tests use realistic, messy data.
42. As the developer, I want an evaluation command that scores the tool against Sets with trusted reference tracklists, in an audio-only mode with comments hidden and in normal mode, so that I know whether a change made identification better or worse.

## Implementation Decisions

- **Architecture** (see ADR 0001 and ADR 0002): plain code does the mechanics, and one Pydantic AI agent does the judgement. Before the agent starts, code downloads the audio, takes the 15-second grid of Clips, and builds a draft Tracklist. The agent then reads the comments, follows up on gaps and conflicts, and returns the final Tracklist.
- **Set Source** (YouTube boundary): turns any YouTube link into a video ID, and fetches Set info (title, channel, duration, description), comments and audio. Comments are fetched in two passes, because YouTube's "top" order stops after about 1,000 comments: the top comments with replies under the top threads (up to about 2,000 replies), and up to 3,000 of the newest comments. Top-level comments are then ordered by likes. It optionally uses browser cookies. It is built on yt-dlp, which needs Deno for YouTube's player challenges.
- **Comment filter**: keeps the description, comments containing a timestamp, and replies to track-ID questions, and drops everything else before the agent sees it. On the design Set this keeps about 210 of 1,314 top-level comments.
- **Recogniser** (swap point): takes one Clip and returns a Recognition (a Track, or nothing) or a refusal. The first implementation uses Shazam through the unofficial `shazamio` library.
- **Scanner**: cuts Clips with ffmpeg and runs them through the Recogniser. Its pacing adapts: it sends as fast as accepted; on refusal it waits from 30 seconds, doubling up to about 10 minutes, then continues more slowly; it gives up after about 30 minutes of continuous refusals. Every Recognition is saved as soon as it arrives. It enforces the follow-up Clip ceiling.
- **Set store**: one folder per Set in the user's home directory, named by video ID. It holds the Recognitions (keyed by Clip offset and length), the final Tracklist as Markdown and structured data, and the audio only when `--keep-audio` is set. It supports resume and `--fresh`.
- **Draft builder** (pure): turns Recognitions into a draft Tracklist. It merges consecutive Recognitions of the same Track into one Play, timestamped at the first Clip that heard it. It marks gaps where nothing was recognised, flags lone Recognitions, and flags neighbouring Clips that alternate between two Tracks as possible Blends.
- **Identification agent** (Pydantic AI):
  - Tools:
    - `set_info`
    - `comments`: the filtered comments.
    - `draft_tracklist`
    - `recognise(start, end, every, clip_length)`: follow-up Clips, counted against the ceiling and refused once it is reached.
  - Output: the Tracklist schema below, validated by Pydantic AI.
  - Its instructions encode the rules:
    - Recognition wins over a Conflicting Hint, and the conflict is noted.
    - Agreement makes a Confirmed Play.
    - A more specific Comment Hint names the edit.
    - Lone Recognitions are kept only when backed up.
    - Blends are separate Plays sharing a Timestamp.
    - Whatever remains unidentified is an Unknown Track.
  - The token budget is enforced through the framework's usage limits.
  - In `--comments-only` mode there is no audio, no draft and no `recognise` tool.
- **Tracklist schema**:
  - The Set: video ID, link, title, channel, duration.
  - Ordered Plays, each with:
    - Timestamp in seconds.
    - Track: artist, title, and version text if any; absent for an Unknown Track.
    - Evidence: confirmed, audio only, or comments only.
    - An optional Blend group shared by Plays heard together.
    - Notes, including Conflicting Hints.
  - The schema is versioned, because the Spotify step will read it.
- **Renderer**: prints the terminal view and writes the Markdown file. Timestamp links have the form `https://www.youtube.com/watch?v=<video-id>&t=<seconds>s`.
- **CLI**: built with Typer. The command is `djset <youtube-url>`, with `--fresh`, `--keep-audio`, `--comments-only`, `--cookies-from-browser <browser>` and `--model <provider:model>`.
- **Model selection**:
  - The format is a Pydantic AI `provider:model` string.
  - It is resolved from `--model`, then `DJSET_MODEL`, then the default `anthropic:claude-sonnet-5-5`.
  - API keys come from each provider's standard environment variable.
  - No provider-specific code lives in the agent.
- **Evaluation**:
  - The reference tracklists for the two test Sets are stored as data: Siangyoo 011 (`XNEO0Uq8zNA`, 36 entries) and Siangyoo 014 (`LTFCuhofnN8`, 30 entries).
  - A reference entry counts as found if the run lists the same artist and title, ignoring version text, within 45 seconds.
  - Stretches the reference doesn't name are excluded.
  - It reports the share of reference entries found and the number of wrong Plays.
  - Each Set runs in an audio-only mode (comments hidden) and in normal mode.
  - Further details are to be agreed later.
- **Stack**: Python 3.12 (the released `shazamio` is reported to break on 3.13+), uv, Pydantic AI, yt-dlp with Deno, shazamio, Typer, ffmpeg (Homebrew).

## Testing Decisions

- **What a good test is:** it runs a whole identification through its public entry point and asserts on the resulting Tracklist and saved files, never on internal calls or intermediate structures.
- **The seam:** there is one, the identification use case. It has four replaceable boundaries:
  - **Set Source**: backed by fixtures of real Set info and comments.
  - **Recogniser**: replays saved Recognitions by Clip offset, and can simulate refusals.
  - **Model**: Pydantic AI's scripted test models, so the agent's tool calls and final answer are deterministic.
  - **Clock**: pacing waits pass instantly.
- **Through this seam, tests cover:**
  - merging and gap marking
  - comment filtering
  - pacing, give-up and resume
  - the Clip ceiling
  - the conflict, edit, lone-Recognition and Blend rules as reflected in the Tracklist
  - comments-only mode
  - rendering and links
  - model selection
- **Fixtures:** metadata and comments for the design Set and both test Sets can be fetched in cloud sessions. Recognitions are captured from real runs on the Mac, since cloud sessions can't download Set audio.
- **Prior art:** none yet. This is the first code in the repository.
- **Evaluation:** separate from tests. It runs only on request, uses the real services, and costs time and money.

## Out of Scope

- **Spotify:** searching, Spotify Matches and playlists. These are the next spec; the rules already decided are under Further Notes.
- **Paid recognition services:** AudD and ACRCloud beyond the swap point.
- **Other sources and platforms:** 1001Tracklists, Mixes.wiki and other human-tracklist sites; SoundCloud, Mixcloud and other non-YouTube sources; live streams.
- **Detecting edits from audio.** Edits are named only through Comment Hints.
- **Other interfaces:** an interactive chat mode (for example "what's at 27:15?"), a web interface, hosting, and multiple users.
- **Enrichment:** BPM, similar tracks and covers.
- **Running the tool in cloud sessions.**

## Further Notes

- **Spotify rules already decided, for the next spec:**
  - A new private playlist per Set, named after the video title, with the YouTube link in its description.
  - It is filled only after the user confirms which Plays to add; all matched Plays are suggested by default.
  - When the exact version isn't on Spotify, the Original becomes the Spotify Match and is marked as such; unmatched Plays stay listed as unmatched.
  - It requires a Development Mode app owned by a Premium account.
  - Current API constraints:
    - Playlists are created through the current-user endpoint and items are added through the items endpoint.
    - Search returns at most 10 results.
    - Sign-ins expire after 6 months.
    - spotipy's add-items request shape needs checking early.
- **Shazam:** access through `shazamio` is unofficial. Rate limits are unpublished (refusals are anecdotally reported after about 100 quick requests), and it can break without notice.
- **What the Sets showed:**
  - The design Set (`yA9girCNcds`) and both test Sets are by the same DJ (Siangyoo). A Set by a different DJ would show whether the tool generalises.
  - The two test Sets' reference tracklists write artist and title in opposite orders.
  - Both reference tracklists contain a few wrong or missing versions.
