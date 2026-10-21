"""Which AI model the agent runs on: a Pydantic AI `provider:model` string from --model, DJSET_MODEL or the default."""

import os
import re
from collections.abc import Mapping

from pydantic_ai import UserError
from pydantic_ai.models import Model, infer_model

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
SETTING = "DJSET_MODEL"


class ModelUnavailable(RuntimeError):
    pass


def chosen_model(option: str | None, environ: Mapping[str, str] = os.environ) -> str:
    return option or environ.get(SETTING) or DEFAULT_MODEL


def load_model(model: Model | str) -> Model:
    """The model, with its provider's API key checked now rather than after a long recognition run."""
    if isinstance(model, Model):
        return model
    try:
        return infer_model(model)
    except UserError as error:
        raise ModelUnavailable(_advice(model, str(error))) from error


def _advice(model: str, message: str) -> str:
    switch = f"or choose another model with --model or {SETTING}"
    if setting := re.search(r"`([A-Z][A-Z0-9_]+)` environment variable", message):
        name = setting.group(1)
        return f"The model {model} needs the {name} setting. Add `export {name}=...` to your shell profile, {switch}."
    if message.startswith("Unknown model"):
        return f"Unknown model {model!r}. Models are written provider:model, such as {DEFAULT_MODEL} or openai:gpt-5.2."
    return f"Can't use the model {model}: {message.split('. ')[0]}. Fix that, {switch}."
