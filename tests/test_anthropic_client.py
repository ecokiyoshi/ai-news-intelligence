from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from app.anthropic_client import (
    DEFAULT_ANTHROPIC_MODEL,
    build_default_client,
    extract_tool_input,
    parse_structured,
    resolve_model,
    response_text,
)
from support_anthropic import FakeClient, NoToolCallClient


class ExampleModel(BaseModel):
    value: int = Field(ge=0, le=100)


def test_resolve_model_prefers_explicit_argument(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-model")
    assert resolve_model("explicit-model") == "explicit-model"


def test_resolve_model_falls_back_to_environment(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-model")
    assert resolve_model(None) == "env-model"


def test_resolve_model_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert resolve_model(None) == DEFAULT_ANTHROPIC_MODEL


def test_build_default_client_constructs_real_sdk_client_lazily(monkeypatch) -> None:
    created = object()
    received_timeouts: list[float] = []

    class FakeAnthropic:
        def __init__(self, *, timeout: float) -> None:
            received_timeouts.append(timeout)

    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    client = build_default_client(12.5)

    assert isinstance(client, FakeAnthropic)
    assert received_timeouts == [12.5]


def test_parse_structured_sends_expected_request_and_validates_result() -> None:
    client = FakeClient(ExampleModel(value=42))

    result = parse_structured(
        client,
        model="test-model",
        system="Do the thing.",
        input_text="payload",
        response_model=ExampleModel,
    )

    assert result == ExampleModel(value=42)
    call = client.messages.calls[0]
    assert call["model"] == "test-model"
    assert call["system"] == "Do the thing."
    assert call["messages"] == [{"role": "user", "content": "payload"}]
    assert call["tool_choice"] == {"type": "tool", "name": "submit_result"}
    assert call["tools"][0]["input_schema"] == ExampleModel.model_json_schema()


def test_extract_tool_input_rejects_missing_tool_call() -> None:
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="no tool call")])
    with pytest.raises(ValueError, match="did not contain the requested structured tool call"):
        extract_tool_input(response, ExampleModel)


def test_parse_structured_raises_when_no_tool_call_is_returned() -> None:
    client = NoToolCallClient()
    with pytest.raises(ValueError, match="structured tool call"):
        parse_structured(
            client,
            model="test-model",
            system="Do the thing.",
            input_text="payload",
            response_model=ExampleModel,
        )


def test_response_text_concatenates_text_blocks_and_ignores_others() -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text=" Hello "),
            SimpleNamespace(type="tool_use", name="ignored", input={}),
            SimpleNamespace(type="text", text="world."),
        ]
    )
    assert response_text(response) == "Hello world."


def test_response_text_handles_empty_content() -> None:
    assert response_text(SimpleNamespace(content=[])) == ""
    assert response_text(SimpleNamespace(content=None)) == ""
