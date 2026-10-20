# DJ Set Track Identification

Working out which Tracks a DJ plays in a recorded Set on YouTube, and when, so they can be listed as a Tracklist and later collected into a Spotify playlist.

## Language

### Sets and Tracks

**Set**:
One recorded DJ performance in a single YouTube video. Each part of a multi-part series is its own Set.
_Avoid_: mix, video, performance

**Track**:
One specific recording, down to its version: an edit or remix is a different Track from its Original.
_Avoid_: song, tune

**Original**:
The released Track that an edit or remix is based on.
_Avoid_: base track, source track

**Play**:
One occurrence of a Track in a Set, starting at a Timestamp. A Track played twice in a Set is two Plays.
_Avoid_: entry, appearance, segment

**Timestamp**:
The moment in a Set where a Play is first heard, even if it is still being blended in.
_Avoid_: cue, start time

**Tracklist**:
The Plays of a Set in order.
_Avoid_: setlist, track listing

**Unknown Track**:
What a Play is shown as when its Track couldn't be identified. Commenters write this as "ID".
_Avoid_: ID, unidentified

### Spotify

**Spotify Match**:
The Spotify track chosen to stand in for a Track. It may be the Track's Original when the exact version isn't on Spotify, and is then marked as such.
_Avoid_: Spotify result, Spotify track
