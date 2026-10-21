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

**Blend**:
Two or more Plays heard at the same time, such as one Track's vocals over another's beat. A pre-made mashup the DJ plays is a single Track, not a Blend.
_Avoid_: mashup, transition

**Timestamp**:
The moment in a Set where a Play is first heard, even if it is still being blended in.
_Avoid_: cue, start time

**Tracklist**:
The Plays of a Set in order.
_Avoid_: setlist, track listing

**Unknown Track**:
What a Play is shown as when its Track couldn't be identified. Commenters write this as "ID".
_Avoid_: ID, unidentified

### Evidence

**Clip**:
A short stretch of a Set's audio sent for recognition.
_Avoid_: sample (in DJ use, a sound lifted from another record), snippet, chunk

**Recognition**:
What a recognition service reports for one Clip: a Track, or nothing.
_Avoid_: match, hit, Shazam result

**Comment Hint**:
A claim, from a Set's description or comments, that a particular Track plays at a particular time.
_Avoid_: comment ID, suggestion

**Confirmed Play**:
A Play supported by both a Recognition and a Comment Hint.
_Avoid_: verified play

**Conflicting Hint**:
A Comment Hint naming a different Track from the one recognition heard at that time.
_Avoid_: disagreement, dispute

### Spotify

**Spotify Match**:
The Spotify track chosen to stand in for a Track. It may be the Track's Original when the exact version isn't on Spotify, and is then marked as such.
_Avoid_: Spotify result, Spotify track

**Playlist**:
The private Spotify playlist made for one Set, holding the Spotify Matches of the Plays the listener chose, in Tracklist order.
_Avoid_: Spotify list
