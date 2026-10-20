# Standalone Pydantic AI agent with a pluggable model

djset is a standalone command-line program whose agent is built with Pydantic AI, rather than a Claude Code skill, so it runs without Claude Code and isn't tied to one AI provider. The model is a single setting (`--model`, or `DJSET_MODEL`) that defaults to a Claude model; OpenAI, Ollama and other providers plug in through Pydantic AI without changes to the agent code.

## Considered Options

- **Claude Code skill driving helper scripts**: the cheapest to build and needs no API key, but only usable inside Claude Code.
- **Claude Agent SDK**: ties the agent to Claude.
- **LangGraph**: suits explicit graphs with checkpoints and human approval steps. Our flow is a fixed pipeline plus one agent loop, and our own code already saves progress per Set.
- **Plain code with no AI**: comment tracklists are too messy to parse with fixed rules, and following up on gaps needs judgement.

## Consequences

- Each run is billed to the user's API key for the chosen provider.
- The mechanics (downloading, Clips, Recognitions, merging consecutive Recognitions) stay plain code. The agent reads comments into Comment Hints, chooses follow-up Clips for gaps and conflicts within a fixed ceiling, and writes the final Tracklist.
