"""The identification agent: reads Comment Hints, follows up on gaps and conflicts, and writes the Tracklist."""

from dataclasses import dataclass

import pydantic_ai
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, ModelSettings, RunContext, ToolDefinition, UsageLimits
from pydantic_ai.models import Model

from djset.draft import build_draft
from djset.links import clock
from djset.progress import Progress
from djset.scanner import CeilingReached, GaveUp, Scanner
from djset.tracklist import Mode, Play, SetInfo

pydantic_ai.BANNER_ENABLED = False  # it would print over the run's progress display

USAGE_LIMITS = UsageLimits(request_limit=60, total_tokens_limit=1_500_000)
"""The AI budget for one run. Input tokens include cached ones, so it runs out sooner than the cost suggests."""

TERMS = """\
You identify the Tracks a DJ plays in one recorded Set from YouTube and return its Tracklist.

Terms:
- Track: one specific recording, down to its version. An edit or remix is a different Track from its Original.
- Play: one occurrence of a Track in the Set, starting at a Timestamp: the moment it is first heard, even while it is \
still being blended in. A Track played twice is two Plays.
- Blend: two or more Plays heard at the same time, such as one Track's vocals over another's beat. A pre-made mashup \
the DJ plays is one Track, not a Blend.
- Clip: a short stretch of the Set's audio sent to a recognition service. Recognition: what the service reported for \
one Clip, a Track or nothing.
- Comment Hint: a claim, in the Set's description or comments, that a particular Track plays at a particular time.
- Unknown Track: a Play nobody could identify. Commenters write this as "ID".
"""

RULES = """\
Rules:
- Every identified Play rests on a Recognition or a Comment Hint. Never name a Track from your own knowledge of the \
DJ or the music.
- Recognition and a Comment Hint agreeing on a Play makes it "confirmed".
- When a Comment Hint names a different Track from the one recognised at that time (a Conflicting Hint), the \
Recognition wins. Keep the recognised Track and add a note such as "A comment says Linkin Park – In the End".
- Recognition usually reports the Original when the DJ plays an edit or remix. When a Comment Hint names the same \
Track with a specific edit, remix or bootleg, use the Comment Hint's version (in `version`); the Play is confirmed.
- A lone Recognition (heard by a single Clip), especially one inside another Play's stretch, is often a \
misrecognition during a transition. Keep it only when a Comment Hint or another Recognition backs it up.
- Tracks heard together are a Blend: separate Plays with the same Timestamp and the same `blend` number.
- A Comment Hint for a stretch that recognition left empty becomes a "comments only" Play.
- A stretch of music longer than about 30 seconds that nothing identifies is an Unknown Track Play, timestamped at its \
start. Leave out silence or talk before the music starts and after it ends.
- Timestamps are whole seconds into the Set. When a Comment Hint's time is within 30 seconds of a Recognition of the \
same Track, use the earlier of the two.
- List Plays in Timestamp order. Notes are for the listener: short, plain, and only when useful.
"""

MODE_INSTRUCTIONS = {
    Mode.NORMAL: """\
How to work:
1. Read the draft Tracklist (draft_tracklist) and the description and comments (comments).
2. Turn comments into Comment Hints. People write "Artist – Title" or "Title – Artist", add emoji and @name credits, \
write Blends as "A x B – Title1 x Title2", or give only a lowercase partial title like "7:50 numb". Replies can answer \
questions like "what's the track at 24:20?". Trust hints more when they have more likes or several commenters agree.
3. Use recognise for follow-up Clips where they can help: gaps, lone Recognitions, Conflicting Hints and possible \
Blends. Shorter Clips more often (say 8-second Clips every 5 seconds), or Clips shifted off the 15-second grid, catch \
Tracks that the grid missed. Follow-up Clips are limited, so spend them where a Play is most likely to be identified.
4. Return the Tracklist. Evidence is "confirmed", "audio only" or "comments only".
""",
    Mode.AUDIO_ONLY: """\
There are no comments in this run, so every identified Play is "audio only".
How to work:
1. Read the draft Tracklist (draft_tracklist).
2. Use recognise for follow-up Clips where they can help: gaps, lone Recognitions and possible Blends. Shorter Clips \
more often (say 8-second Clips every 5 seconds), or Clips shifted off the 15-second grid, catch Tracks that the grid \
missed. Follow-up Clips are limited, so spend them where a Play is most likely to be identified.
3. Return the Tracklist.
""",
    Mode.COMMENTS_ONLY: """\
There is no audio in this run, so every identified Play is "comments only", and Unknown Track Plays come only from \
comments that say a stretch is unidentified ("ID").
How to work:
1. Read the description and comments (comments) and turn them into Comment Hints. People write "Artist – Title" or \
"Title – Artist", add emoji and @name credits, write Blends as "A x B – Title1 x Title2", or give only a lowercase \
partial title like "7:50 numb". Replies can answer questions like "what's the track at 24:20?". Trust hints more when \
they have more likes or several commenters agree.
2. Return the Tracklist. When comments disagree, pick the better-supported Track and note the other.
""",
}


@dataclass
class AgentDeps:
    set_info: SetInfo
    mode: Mode
    comments: str | None
    scanner: Scanner | None
    progress: Progress


class AgentTracklist(BaseModel):
    plays: list[Play]


agent = Agent(
    deps_type=AgentDeps,
    output_type=AgentTracklist,
    instructions=TERMS,
    model_settings=ModelSettings(cache=True),
    retries=3,
    defer_model_check=True,
)


@agent.instructions
def _rules_for_mode(ctx: RunContext[AgentDeps]) -> str:
    return RULES + "\n" + MODE_INSTRUCTIONS[ctx.deps.mode]


async def _with_comments(ctx: RunContext[AgentDeps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if ctx.deps.comments is not None else None


async def _with_audio(ctx: RunContext[AgentDeps], tool: ToolDefinition) -> ToolDefinition | None:
    return tool if ctx.deps.scanner is not None else None


@agent.tool
async def set_info(ctx: RunContext[AgentDeps]) -> str:
    """The Set's title, channel and length."""
    info = ctx.deps.set_info
    return f"Title: {info.title}\nChannel: {info.channel}\nLength: {clock(info.duration)} ({info.duration} seconds)"


@agent.tool(prepare=_with_comments)
async def comments(ctx: RunContext[AgentDeps]) -> str:
    """The Set's description, and its comments that may hold Comment Hints: those with a timestamp, pinned or \
uploader comments, and replies to track-ID questions."""
    assert ctx.deps.comments is not None
    return ctx.deps.comments


@agent.tool(prepare=_with_audio)
async def draft_tracklist(ctx: RunContext[AgentDeps]) -> str:
    """The draft Tracklist built from every Recognition so far, including follow-ups.

    It merges consecutive Recognitions of one Track into a Play, flags lone Recognitions and Tracks recognised within \
another Play's stretch (possible Blends), and lists gaps where nothing was recognised.
    """
    assert ctx.deps.scanner is not None
    return build_draft(list(ctx.deps.scanner.known.values())).text()


@agent.tool(prepare=_with_audio)
async def recognise(ctx: RunContext[AgentDeps], start: int, end: int, every: int, clip_length: int) -> str:
    """Recognise follow-up Clips: one Clip of `clip_length` seconds every `every` seconds from `start` until `end`.

    Clips already recognised cost nothing. New Clips count against a fixed limit for the run, and a request that \
needs more new Clips than are left is refused.

    Args:
        start: Seconds into the Set where the first Clip starts.
        end: Seconds into the Set before which the last Clip starts.
        every: Seconds between the starts of consecutive Clips, from 3 to 60.
        clip_length: Length of each Clip in seconds, from 4 to 20. Recognition needs at least 6 to be reliable.
    """
    scanner = ctx.deps.scanner
    assert scanner is not None
    if not 0 <= start < end <= scanner.duration:
        raise ModelRetry(f"start and end must satisfy 0 <= start < end <= {scanner.duration}.")
    if not 3 <= every <= 60 or not 4 <= clip_length <= 20:
        raise ModelRetry("every must be from 3 to 60 seconds and clip_length from 4 to 20 seconds.")
    clips = scanner.clips(start, end, every, clip_length)
    if scanner.follow_ups_left == 0 and any(clip not in scanner.known for clip in clips):
        return "No follow-up Clips are left. Finish the Tracklist with what you have; mark what remains Unknown Track."
    ctx.deps.progress.note(f"Listening again to {clock(start)}–{clock(end)} ({len(clips)} Clips)")
    try:
        recognitions = await scanner.follow_up(clips)
    except CeilingReached as limit:
        return (
            f"Refused: that needs {limit.needed} new Clips but only {limit.left} follow-up Clips are left. "
            "Ask for a shorter stretch or a longer interval."
        )
    except GaveUp:
        scanner.follow_ups_left = 0
        return "The recognition service is refusing requests, so no more Clips can be recognised. Finish now."
    lines = [f"{scanner.follow_ups_left} follow-up Clips left. {_budget_left(ctx)}"]
    for recognition in recognitions:
        heard = str(recognition.track) if recognition.track else "nothing recognised"
        lines.append(f"{clock(recognition.start)} ({recognition.length}s Clip): {heard}")
    return "\n".join(lines)


def _budget_left(ctx: RunContext[AgentDeps]) -> str:
    turns = (USAGE_LIMITS.request_limit or 0) - ctx.usage.requests
    tokens = (USAGE_LIMITS.total_tokens_limit or 0) - ctx.usage.total_tokens
    return f"AI budget left: {turns} turns and {tokens:,} tokens; return the Tracklist well before either runs out."


async def identify_plays(deps: AgentDeps, model: Model | str) -> list[Play]:
    """Run the agent and return its Plays. Raises UsageLimitExceeded when the AI budget runs out."""
    result = await agent.run(
        f"Identify the Plays in the Set {deps.set_info.title!r} and return its Tracklist.",
        model=model,
        deps=deps,
        usage_limits=USAGE_LIMITS,
    )
    return result.output.plays
