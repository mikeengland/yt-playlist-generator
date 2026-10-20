# Audio recognition is the main source; comments are hints

We identify Plays mainly by sending Clips of the Set's audio to a recognition service, and use the description and comments only as Comment Hints that confirm, refine or fill gaps. The Set we designed against (`yA9girCNcds`, one hour, about 1,300 comments) had no tracklist in its description and its best comment listed 6 Tracks against an estimated 60+ Plays, so a comments-first tool would identify under 10% of it.

## Considered Options

- **Comments first, recognition only for gaps**: rejected, because Sets like the one above have too little in their comments.
- **1001Tracklists**: rejected for now. It has no public API, scraping it conflicts with its terms, and it is weighted towards electronic music rather than the hip-hop/R&B Sets this is for.

## Consequences

- Recognition starts with Shazam through the unofficial `shazamio` library: free, but it can refuse requests or break without notice. Recognition sits behind a single swap point so a licensed service (AudD, ACRCloud) can replace or join it.
- The DJ changes Track every 30–60 seconds, so Clips are taken every 15 seconds, and Shazam's request limits make a run take roughly 15–20 minutes per hour of Set.
- Recognition usually reports the Original when the DJ plays an edit. When a Comment Hint names the edit and agrees with the Recognition, the Play takes the Comment Hint's more specific Track.
