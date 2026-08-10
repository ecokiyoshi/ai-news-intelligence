"""Shared fakes for testing Claude (Anthropic Messages API)-backed providers.

Not a test module itself (pytest's default ``test_*.py``/``*_test.py`` discovery skips
it); imported by the ``test_anthropic_*.py`` modules alongside it in ``tests/``.
"""

from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel


def to_jsonable(value: Any) -> Any:
    """Recursively convert pydantic models/SimpleNamespace test fixtures into plain data.

    Mirrors how the real Anthropic SDK hands back a forced tool call's ``input`` as a
    plain ``dict`` parsed from JSON: whatever object graph a test builds (real response
    models, or ``SimpleNamespace`` stand-ins for malformed provider output), this turns
    it into the same shape so ``response_model.model_validate(...)`` sees a realistic
    payload.
    """

    if isinstance(value, BaseModel):
        return {key: to_jsonable(item) for key, item in value.__dict__.items()}
    if isinstance(value, SimpleNamespace):
        return {key: to_jsonable(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


class FakeMessages:
    """Fakes the Messages API's ``.create`` for one forced structured tool call."""

    def __init__(self, parsed: Any = None, error: Exception | None = None) -> None:
        self.parsed = parsed
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        tool_name = kwargs["tool_choice"]["name"]
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name=tool_name,
                    input=to_jsonable(self.parsed),
                )
            ]
        )


class NoToolCallMessages:
    """Fakes a Messages API response that never emits the requested tool call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="no tool call")])


class SequencedMessages:
    """Fakes a Messages API client that returns one queued result per call."""

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        tool_name = kwargs["tool_choice"]["name"]
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="tool_use", name=tool_name, input=to_jsonable(result))
            ]
        )


class FakeClient:
    """Fakes the top-level injectable Anthropic client (``client.messages``)."""

    def __init__(self, parsed: Any = None, error: Exception | None = None) -> None:
        self.messages = FakeMessages(parsed=parsed, error=error)


class NoToolCallClient:
    def __init__(self) -> None:
        self.messages = NoToolCallMessages()


class SequencedClient:
    def __init__(self, results: list[Any]) -> None:
        self.messages = SequencedMessages(results)


def call_input_text(call: dict[str, Any]) -> str:
    """Extract the user input text sent in one ``messages.create`` call."""

    return call["messages"][0]["content"]
