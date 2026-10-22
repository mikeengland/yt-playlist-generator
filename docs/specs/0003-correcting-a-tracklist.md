# Spec: Correcting a Tracklist in your own words (djset v3)

## Problem Statement

Recognition sometimes names the wrong Track, and I know what the right one is. Shazam heard "Quantum Roots – NOKIA" at 2:40, but it's Drake's "NOKIA". The Spotify proposal then lists it as not on Spotify, and the only thing I can do at the prompt is leave Plays out. I can't tell the tool what the Track really is. Today the only fix is editing `tracklist.json` by hand. The same goes for a Track recognition missed, a Play that isn't in the Set at all, or a Timestamp that is off.

## Solution

I can correct the Tracklist by describing the change in my own words, like "2:40 is Drake – Nokia", and an AI model works out which Plays to change. I can do this in two places: with `--correct "..."` on any run, with or without `--spotify`, and by typing the Correction at the Spotify prompt instead of numbers. The corrected Tracklist is saved, and its corrected Plays are marked as based on the listener. At the Spotify prompt, the proposal is then made again, with the corrected Tracks searched on Spotify, and I'm asked again.

## User Stories

1. As a listener, I want to name the Track a Play really is, in my own words ("2:40 is Drake – Nokia"), so that a wrong Recognition doesn't stay in my Tracklist.
2. As a listener, I want to refer to Plays the way I see them, by Timestamp or by the Track shown ("the Nokia one"), so that I don't need to know any numbering.
3. As a listener, I want to add a Play recognition missed ("there's Usher – Yeah! at 12:10"), including one Blended with another Play, so that the Tracklist is complete.
4. As a listener, I want to remove a Play that isn't in the Set, so that a misrecognition doesn't end up in my Playlist.
5. As a listener, I want to make a Play an Unknown Track ("I don't think 5:00 is right"), so that a doubtful Track isn't presented as identified.
6. As a listener, I want to move a Play's Timestamp, so that its link goes to the right moment.
7. As a listener, I want several changes in one Correction ("2:40 is Drake – Nokia and remove 7:00"), so that I don't have to repeat myself.
8. As a listener, I want `--correct "..."` to work on any run, with or without `--spotify`, and to be repeatable, so that I can fix a saved Tracklist without making a Playlist.
9. As a listener, I want to type a Correction at the Spotify prompt, so that I can fix a Track as soon as I see it is "not on Spotify".
10. As a listener, I want the proposal made again after a Correction at the prompt, with the corrected Tracks searched on Spotify, so that the Playlist gets the right Track.
11. As a listener, I want to see exactly what changed ("2:40 Quantum Roots – NOKIA → Drake – NOKIA"), so that I can check the model understood me.
12. As a listener, I want to be told when the model couldn't make a change, and why, so that I can say it differently.
13. As a listener, I want corrected Plays marked as based on what I said, with the Track they replaced in their notes, so that I can tell my Corrections from what the tool found.
14. As a listener, I want the corrected Tracklist saved (`tracklist.md` and `tracklist.json`), so that later runs and later Playlists use it.
15. As a listener, I want a Correction the model can't make, or a failing model, at the Spotify prompt to leave me at the prompt, so that a typo or an outage doesn't throw away the proposal.

## Implementation Decisions

- **Correction use case** (`correct.py`): the third seam tests drive. It takes a Tracklist, the listener's Correction and its boundaries: the model, Progress and the store root. It asks the model for changes, applies them, saves the corrected Tracklist and returns it with the changes made and anything the model couldn't do.
- **The model proposes changes; code applies them.** The model never rewrites the Tracklist. It returns a list of changes to the numbered Plays it was shown:
  - **name**: the Track a Play really is, or null to make it an Unknown Track.
  - **add**: a new Play at a Timestamp, optionally Blended with a listed Play.
  - **remove**: a Play that isn't in the Set.
  - **move**: a Play's new Timestamp. Moving a Play of a Blend moves the whole Blend.

  Plays the changes don't touch stay exactly as they were. A change to a Play number that doesn't exist, a Timestamp past the end of the Set, or a change to a removed Play is sent back to the model to fix (Pydantic AI's `ModelRetry`).
- **What the model sees**: the Set's title and length, the numbered Tracklist (Timestamp in `m:ss` and seconds, Track, evidence, Blend and notes) and the Correction. Nothing else. In particular the Spotify proposal never reaches it: Spotify's Developer Policy forbids feeding Spotify content into an AI model. A Correction typed at the Spotify prompt is the listener's own words.
- **Rules for the model**:
  - Name only Tracks the listener named. Unlike identification, the listener's word is the evidence, so it outranks Recognitions and Comment Hints.
  - Write names as they're usually written (capitalisation, obvious typos), but never swap in a different Track.
  - A partial Correction ("2:40 is by Drake") keeps the rest of the Track as it was.
  - Remove a Play only when the listener says it isn't in the Set. Leaving a Track out of the Playlist isn't a Correction: the model replies that Plays are left out by number at the prompt.
  - When it can't tell what to change, it changes nothing and replies in one short sentence.
- **Evidence**: a new value, `listener`, for a Play named or added by a Correction. A named Play's notes are replaced by the Track it replaced ("Was Quantum Roots – NOKIA"). Moving a Play keeps its evidence. The identification agent may not use `listener`: its output is sent back if it does.
- **Order**: after the changes, Plays are put in Timestamp order and each Blend gets one Timestamp and a number, as identification already does. That shared step moves to `tracklist.py`.
- **Model**: the same `--model` / `DJSET_MODEL` setting as identification. It is loaded only when there is a Correction, so a saved Tracklist still makes a Playlist without an AI key. One Correction is a single short request, allowed a few retries.
- **Playlist use case**: the Chooser may answer with a Correction instead of a choice. Making the Playlist then stops with nothing made, and returns the Correction. The playlist use case still involves no AI model.
- **CLI**:
  - `--correct TEXT`, repeatable. Corrections are applied in order after identification (or loading the saved Tracklist) and before the Tracklist is printed. The changes are printed under it.
  - At the Spotify prompt, an answer made only of numbers, ranges, Enter or `n` is a choice as before. Any other answer is a Correction. The CLI corrects the Tracklist, prints the changes and makes the proposal again. Searches already saved aren't repeated, so only the corrected Tracks are searched.
  - The prompt asks for a Correction even when no Track was found on Spotify, since a Correction may be what finds one.
  - A model failure while correcting at the prompt is printed, and the proposal is shown again. With `--correct` it stops the run, with the Tracklist unchanged.

## Testing Decisions

- **The seam**: the correction use case, driven like identification, with the scripted model. Tests assert on the corrected Tracklist, the saved files, the changes reported and what the model was shown.
- **Through this seam, tests cover**:
  - naming a Play's Track, keeping its Timestamp and Blend, marking it `listener` and noting what it replaced
  - making a Play an Unknown Track
  - adding a Play, and adding one into a Blend
  - removing a Play, and removing one Play of a two-Play Blend
  - moving a Play and moving a Blend
  - several changes in one Correction
  - Plays the changes don't touch staying the same
  - an impossible change sent back to the model, and fixed
  - the model's reply when it changes nothing
  - the model being shown exactly the numbered Tracklist and the Correction
  - a model that keeps proposing impossible changes, which fails the Correction and leaves the Tracklist unchanged
- **Through the playlist seam**: a Chooser answering with a Correction makes nothing and returns the Correction, and a corrected Tracklist only searches Spotify for the new Track.
- **Through the identification seam**: the identification agent's use of `listener` evidence is sent back.
- **The prompt's parsing** (numbers and ranges, or a Correction) is tested at the edge, like links.

## Out of Scope

- Updating a made Playlist after a Correction, as in v2. `--fresh` makes a new Playlist, but it also identifies the Set again and so forgets the Corrections.
- Keeping Corrections across `--fresh`, or replaying them on a new identification.
- Undo. A Correction can be undone by another Correction.
- Correcting with no AI model, by a fixed syntax.
- Asking the listener follow-up questions: the model replies once and the listener tries again.
- Choosing a different Spotify Match for a Play: the Correction changes the Track, and matching stays plain code.
