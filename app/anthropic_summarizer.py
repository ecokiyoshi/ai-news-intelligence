"""Claude-backed implementation of the summarizer interface."""

from typing import Any, Protocol

from app.anthropic_client import (
    AnthropicClient,
    build_default_client,
    resolve_model,
    response_text,
)
from app.summarization import EmptySummaryResultError, SummaryResult

SUMMARY_INSTRUCTIONS = """\
Summarize only the supplied article text. Be factual and concise, and do not invent information.
Preserve important names, organizations, dates, and numbers. Keep the input language when possible.
Return only the summary text.
"""


class MessagesClient(Protocol):
    """Minimal Messages API surface used by the provider."""

    def create(self, *, model: str, max_tokens: int, system: str, messages: list[dict]) -> Any:
        """Create a message response."""


class AnthropicSummarizerClient(Protocol):
    """Minimal injectable Anthropic client surface used by the provider."""

    messages: MessagesClient


class AnthropicSummarizer:
    """Summarize article text with the Anthropic Messages API."""

    def __init__(
        self,
        *,
        client: AnthropicClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        max_tokens: int = 2048,
    ) -> None:
        self.client = client if client is not None else build_default_client(timeout)
        self.model = resolve_model(model)
        self.max_tokens = max_tokens

    def summarize(self, text: str) -> SummaryResult:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SUMMARY_INSTRUCTIONS,
            messages=[{"role": "user", "content": text}],
        )
        summary = response_text(response)
        if not summary:
            raise EmptySummaryResultError("summarizer returned an empty summary")
        return SummaryResult(summary=summary)
