# Spec: Spotify playlist from a Tracklist (djset v2)

## Problem Statement

`djset` gives me a Set's Tracklist, but the Tracks I want to keep listening to are on Spotify, and finding 40–60 of them again by hand, one search at a time, takes longer than the Set itself. Spotify's search is fuzzy, so the first result is often a karaoke cover, a sped-up version or a remix of the Track the DJ actually played. And when the DJ played an edit, the edit usually isn't on Spotify at all, but its Original is.

## Solution

`djset <youtube-link> --spotify` takes the Set's Tracklist (the saved one, or a new one identified first), finds a Spotify Match for each identified Play, shows me the proposed Playlist, and makes it as a new private Spotify playlist once I confirm which Plays to add. A Track whose exact version isn't on Spotify gets its Original, marked as such. Plays with no Spotify Match are listed as unmatched, and Unknown Tracks are left out. The tool signs in to Spotify through my own Development Mode app and remembers the sign-in.

## User Stories

### Making a Playlist

1. As a listener, I want to add `--spotify` to the `djset` command, so that one command takes me from a YouTube Set to a Spotify playlist.
2. As a listener, I want `--spotify` on a Set I've already identified to use its saved Tracklist, so that the Playlist doesn't cost another 15–20 minute run.
3. As a listener, I want `--spotify` on a Set I haven't identified yet to identify it first, so that I don't need two commands.
4. As a listener, I want `--spotify` with `--comments-only` to use the comments-only Tracklist, so that a quick Tracklist can become a quick Playlist.
5. As a listener, I want the Playlist created as a new private playlist named after the Set's video title, with the YouTube link in its description, so that I know where it came from and it stays off my public profile.
6. As a listener, I want the Playlist's Tracks in the Tracklist's order, so that it plays like the Set.
7. As a listener, I want a Track played twice in a Set added once, so that the Playlist has no duplicates.
8. As a listener, I want each Play of a Blend matched separately, so that both Tracks I heard end up in the Playlist.
9. As a listener, I want Unknown Tracks left out of the Playlist, so that it only holds Tracks someone identified.

### Choosing what to add

10. As a listener, I want to see every proposed Spotify Match next to the Play's Timestamp and Track before anything is created, so that I can spot wrong matches.
11. As a listener, I want every matched Play suggested by default, so that pressing Enter adds them all.
12. As a listener, I want to leave out Plays by typing their numbers or ranges ("3 7-9"), so that I can drop a wrong match or a Track I don't like.
13. As a listener, I want to cancel without creating anything, so that I can look at the matches first and decide later.
14. As a listener, I want a `--yes` option that adds every matched Play without asking, so that I can make Playlists without being at the keyboard.
15. As a listener, I want Plays with no Spotify Match listed as not on Spotify, so that I know which Tracks to look for elsewhere.
16. As a listener, I want a Spotify Match that is the Track's Original marked as such, so that I know it isn't the version the DJ played.
17. As a listener, I want a link to the Playlist when it is made, so that I can open it straight away.

### Matching

18. As a listener, I want a Spotify Match to be by the same artist as the Track, so that covers, karaoke and tribute versions aren't chosen.
19. As a listener, I want a remix, live, sped-up, instrumental or other version never chosen for a Track that is the Original, so that I hear what the DJ played.
20. As a listener, I want remastered, radio edit, single, album, explicit and clean releases accepted as the same Track, so that a Track isn't left unmatched over a release label.
21. As a listener, I want featured artists, "feat." and "ft." credits, "&", "x" and commas between artists, accents and punctuation not to stop a match, so that "Chris Brown Feat. Tyga" finds "Chris Brown, Lil Wayne, Tyga".
22. As a listener, I want a Play whose Track names an edit or remix matched to that exact version when Spotify has it, and to its Original otherwise, so that I get the closest thing to what was played.

### Running it

23. As a listener, I want to sign in to Spotify once in my browser and have the sign-in remembered, so that later Playlists need no sign-in.
24. As a listener, I want to be asked to sign in again when Spotify's six-month sign-in runs out, so that an expired sign-in doesn't break the tool.
25. As a listener, I want the Spotify sign-in done before a long identification starts, so that a setup problem shows up straight away rather than after 20 minutes.
26. As a listener, I want a clear message, with setup steps, when the Spotify app isn't set up (no Client ID, wrong redirect address) or Spotify refuses it (the owner's Premium has lapsed, or I'm not on the app's user list), so that I can fix it.
27. As a listener, I want the run to wait and retry when Spotify says it is busy, and stop with a clear message if it stays busy, so that rate limits and quotas don't kill a run or hang it.
28. As a listener, I want every Spotify search saved as it arrives, so that a stopped run picks up where it stopped and searches aren't repeated.
29. As a listener, I want running `--spotify` again on a Set whose Playlist is already made to show its link instead of making another, so that I don't end up with duplicate Playlists.
30. As a listener, I want to make a new Playlist for a Set whose Playlist I deleted on Spotify, with `--new-playlist` or under a new name with `--playlist-name`, so that a deleted Playlist doesn't block me and I don't have to identify the Set again.
31. As a listener, I want a Playlist whose creation was interrupted part-way through adding Tracks to be finished by the next run, so that I don't get a half-empty Playlist and a second one.

### Building and maintaining it

32. As the developer, I want the tests to run without Spotify or a browser, so that they are fast, free and work in cloud sessions.
33. As the developer, I want Spotify behind a single interface, so that the playlist use case is tested against a fake catalogue and the real client is tested against recorded request shapes.

## Implementation Decisions

- **No AI model**: making a Playlist is plain code and needs no AI key. Spotify's Developer Policy also forbids feeding Spotify content into an AI model, so Spotify data must never reach the agent.
- **Playlist use case** (`playlist.py`): the second seam tests drive. It takes a Tracklist and four boundaries:
  - **Spotify**: search, create a private playlist, add items.
  - **Chooser**: shown the proposal, returns the suggestions to add, or none to cancel.
  - **Clock**: waits when Spotify is busy.
  - **Progress**.

  Steps:
  1. If the Set's Playlist is already made, it shows that Playlist, and first finishes adding its Tracks if a run stopped part-way. It makes a new one instead when asked to (`--new-playlist`) or when given a name (`--playlist-name`) other than the made one's. The new one replaces the old in `playlist.json`; the saved searches are reused. Deleting a playlist on Spotify only unfollows it, and the tool doesn't ask Spotify whether that happened, so making another is the listener's call.
  2. Otherwise it searches Spotify once for each distinct identified Track not searched before, and saves each result as it arrives.
  3. It builds the proposal and asks the Chooser.
  4. It creates the Playlist, saves it before adding anything, then adds its Tracks in chunks of 100, saving progress after each.
- **Proposal**:
  - A numbered suggestion for each distinct Spotify Match, in Tracklist order, carrying the Plays it stands for. Repeated Tracks, and different Tracks that fall back to the same Original, become one suggestion.
  - The unmatched Plays, one per Track.
  - Unknown Tracks are not part of it.
- **Matcher** (`matching.py`, pure): builds the searches for a Track and picks the Spotify Match from the results.
  - Names are compared normalised: case and accents are ignored, "&" counts as "and", and apostrophes, full stops and other punctuation are dropped.
  - A title splits into its main part, featured-artist credits (dropped), neutral release labels (dropped: remaster, radio/single/album/extended/original edit, mix or version, explicit, clean, mono, stereo, deluxe, "from ..." and years) and version text (everything else in brackets or after " - ").
  - The artist must overlap: a Track's artist is split on commas, "&", "x", "feat.", "ft.", "featuring", "with" and "vs", and at least one name must equal one of the Spotify track's artists, or the whole string must. A leading "The" is ignored.
  - Same Track:
    - The whole titles are equal once featured credits are dropped, or
    - the main parts are equal and the versions agree. A Track without version text needs a result without version text. A Track's version words must all appear in the result's version text.
  - A Track's version is its `version` field, or version text in its title (Shazam's way of naming a remix).
  - Original fallback: when a Track names an edit or remix (its version has a word such as remix, edit, bootleg, mix, flip, rework, cut, dub or VIP) and no result is that version, a result with the same artist, the same main title and no version text becomes the Spotify Match, marked as the Original.
  - Among acceptable results, Spotify's order decides.
  - Searches, most specific first, stopping at the first exact Spotify Match. An Original found on the way is used only if no search finds the exact version:
    - For a Track with a version: the version-specific free-text search ("main title, version, artist").
    - Fielded: `track:"<main title>" artist:"<first artist>"`.
    - Free text: "first artist, main title".
  - Every search asks for 10 results, Spotify's maximum.
- **Spotify client** (`spotify.py`): a small client over Spotify's Web API using `httpx2` (already installed with Pydantic AI), not spotipy. Six requests are all it needs, and spotipy's add-items call sends a bare list where the current API documents `{"uris": [...]}`. It uses the endpoints that survive the February 2026 Development Mode changes:
  - `GET /search` with `type=track` and `limit=10`, with no market: the signed-in user's country applies.
  - `POST /me/playlists` with `public: false`.
  - `POST /playlists/{id}/items` with a `uris` body, at most 100 per request.
- **Sign-in**:
  - Authorization Code with PKCE, so there is no client secret. The only setting is `SPOTIFY_CLIENT_ID`, from the listener's own Development Mode app (its owner needs Premium; up to 5 allowlisted users).
  - Redirect URI: `http://127.0.0.1:8888/callback`. Spotify no longer accepts `localhost`.
  - Scope: `playlist-modify-private`.
  - The browser opens Spotify's sign-in page, and a one-request local server on that port receives the redirect and checks its `state`.
  - Tokens are saved, readable only by the user, in `~/djset/spotify-token.json` (or under `$DJSET_HOME`). Access tokens are refreshed when they expire. A refused refresh (`invalid_grant`, as after the six-month limit) means signing in again.
- **Busy Spotify**:
  - A 429 or an unreachable Spotify makes the use case wait (`Retry-After` when given, otherwise from 5 seconds doubling to a minute) and retry.
  - It stops with a clear message, everything so far saved, once waiting would pass 5 minutes. A `QUOTA_EXCEEDED` with a long `Retry-After` stops at once.
  - A 403 stops with the Development Mode explanation.
- **Set store**: the Set's folder gains `spotify-searches.jsonl` (each Track searched and its Spotify Match or nothing) and `playlist.json` (the Playlist's ID, link, name, chosen Spotify Matches and how many are added). `--fresh` forgets both, like everything else saved for the Set. A later `--spotify` then makes a new Playlist, and the old one stays on Spotify.
- **CLI**:
  - `--spotify`, `--yes`, `--new-playlist` and `--playlist-name` join `djset`.
  - With `--spotify`, the Client ID is checked and the sign-in done before identification. The Tracklist is printed as before, then the proposal.
  - The prompt takes Enter (add all), numbers and ranges to leave out, or `n` (cancel).
  - The run ends with the Playlist's link.

## Testing Decisions

- **What a good test is**: as in v1, it runs a whole use case through its entry point and asserts on what reaches a boundary (the searches, the Playlist created, the items added), the proposal the Chooser saw, and saved files. It never asserts on internal calls.
- **The seam**: the playlist use case, with a fake Spotify that answers searches from a small catalogue of Spotify-shaped tracks (names like "Love Me JeJe - FNKY Remix", "Wannabe - Remastered 2011", covers by other artists) and records every playlist and item added, a scripted Chooser and the fake Clock.
- **Through this seam, tests cover**:
  - exact matches, covers and other versions refused, release labels accepted
  - featured and joined artist names
  - edits matched exactly or to their Original
  - repeated Tracks and Blends
  - unmatched Plays and Unknown Tracks
  - leaving Plays out, and cancelling
  - resuming searches and a part-made Playlist
  - a made Playlist not made twice, unless a new one or a new name is asked for
  - busy Spotify, waits and giving up
- **The real client**: tested against `httpx2.MockTransport` for the documented request shapes (search parameters, the private playlist body, the `uris` body), PKCE (the challenge is the S256 of the verifier sent to the token endpoint), token saving, refreshing and re-sign-in on `invalid_grant`, and 429/403 handling. The local sign-in server is tested with a real request to a free port.
- **The prompt's parsing** ("3 7-9") is tested at the edge, like links.
- **Fixtures**: cloud sessions can't sign in to Spotify, so the catalogue is written by hand in Spotify's naming style, using Tracks from the reference Sets.

## Out of Scope

- Updating a made Playlist when the Tracklist changes, or adding to an existing playlist.
- Matching with an AI model.
- Choosing among several candidate Spotify Matches per Play; the prompt only leaves Plays out.
- Public or collaborative playlists, cover images and other services (Apple Music, YouTube Music, Tidal).
- Extended Quota Mode: Spotify accepts it only from organisations.
- Running the sign-in in cloud sessions: it needs a browser on the same machine.

## Further Notes

- **Spotify's current constraints (October 2026)**:
  - Development Mode apps require the owner's active Premium, allow 5 users, and are subject to per-developer quotas that answer 429 with reason `QUOTA_EXCEEDED`.
  - `POST /playlists/{id}/tracks` and the other `/tracks` playlist endpoints were removed in favour of `/items`.
  - Search's `limit` is at most 10 (default 5).
  - Refresh tokens last 6 months from sign-in, and refreshing doesn't extend them.
  - Users who aren't on the app's allowlist can sign in but get 403 from the API.
- **Spotify's Developer Policy** forbids using Spotify content to train, or otherwise feeding it into, an AI model.
