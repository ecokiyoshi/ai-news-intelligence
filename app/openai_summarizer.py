"""OpenAI-backed implementation of the summarizer interface."""

import os
from typing import Any, Protocol

from openai import OpenAI

from app.summarization import EmptySummaryResultError, SummaryResult

DEFAULT_OPENAI_MODEL = "gpt-5.5"
SUMMARY_INSTRUCTIONS = """\
Summarize only the supplied article text. Be factual and concise, and do not invent information.
Preserve important names, organizations, dates, and numbers. Keep the input language when possible.
Return only the summary text.
"""


class ResponsesClient(Protocol):
    """Minimal Responses API surface used by the provider."""

    def create(self, *, model: str, instructions: str, input: str) -> Any:
        """Create a model response."""


class OpenAIClient(Protocol):
    """Minimal injectable OpenAI client surface used by the provider."""

    responses: ResponsesClient


class OpenAISummarizer:
    """Summarize article text with the OpenAI Responses API."""

    def __init__(
        self,
        *,
        client: OpenAIClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL

    def summarize(self, text: str) -> SummaryResult:
        response = self.client.responses.create(
            model=self.model,
            instructions=SUMMARY_INSTRUCTIONS,
            input=text,
        )
        summary = response.output_text.strip()
        if not summary:
            raise EmptySummaryResultError("summarizer returned an empty summary")
        return SummaryResult(summary=summary)
