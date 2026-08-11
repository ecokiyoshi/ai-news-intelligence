"""Shared helpers for Claude (Anthropic Messages API) structured-output providers.

Every Claude-backed provider in this package needs the same two things: a lazily
constructed Anthropic client, and a way to force the model to return one structured,
schema-validated result. Anthropic's Messages API has no direct equivalent of the
OpenAI Responses API's ``text_format`` parsing helper, so structured output is obtained
by forcing a single tool call whose JSON Schema is derived from a Pydantic model, then
validating the tool call's input against that model. This module centralizes that
pattern so individual provider modules stay focused on their instructions and payloads.
"""

import os
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"
DEFAULT_STRUCTURED_MAX_TOKENS = 8192
STRUCTURED_TOOL_NAME = "submit_result"

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class MessagesClient(Protocol):
    """Minimal Messages API surface used by the Claude-backed providers."""

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> Any:
        """Create a message response."""


class AnthropicClient(Protocol):
    """Minimal injectable Anthropic client surface used by the providers."""

    messages: MessagesClient


def resolve_model(model: str | None) -> str:
    """Resolve the configured model, falling back to ANTHROPIC_MODEL then the default."""

    return model or os.getenv("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL


def build_default_client(timeout: float) -> AnthropicClient:
    """Construct the real Anthropic SDK client, imported lazily.

    Importing the ``anthropic`` package only when an explicit client is not injected
    keeps the dependency optional for callers who only use the deterministic local
    providers or inject their own fake/test client.
    """

    from anthropic import Anthropic

    return Anthropic(timeout=timeout)


def extract_tool_input(
    response: Any,
    response_model: type[ResponseModelT],
    *,
    tool_name: str = STRUCTURED_TOOL_NAME,
) -> ResponseModelT:
    """Extract and validate the forced tool call's input from a Messages API response."""

    content = getattr(response, "content", None) or []
    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != tool_name:
            continue
        block_input = getattr(block, "input", None)
        if block_input is None:
            continue
        return response_model.model_validate(block_input)
    raise ValueError("Claude response did not contain the requested structured tool call")


def parse_structured(
    client: AnthropicClient,
    *,
    model: str,
    system: str,
    input_text: str,
    response_model: type[ResponseModelT],
    max_tokens: int = DEFAULT_STRUCTURED_MAX_TOKENS,
    tool_name: str = STRUCTURED_TOOL_NAME,
) -> ResponseModelT:
    """Force a single structured tool call and return the validated result."""

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": input_text}],
        tools=[
            {
                "name": tool_name,
                "description": "Submit the required structured result.",
                "input_schema": response_model.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": tool_name},
    )
    return extract_tool_input(response, response_model, tool_name=tool_name)


def response_text(response: Any) -> str:
    """Concatenate plain text blocks from a Messages API response."""

    content = getattr(response, "content", None) or []
    parts = [
        getattr(block, "text", "")
        for block in content
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts).strip()
