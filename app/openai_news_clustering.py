"""OpenAI-backed grouping provider for similar priority news."""

import json
import os
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field, StrictInt

from app.news_clustering import (
    MAX_CLUSTER_ARTICLES,
    NewsClusterGrouping,
    NewsClusterSource,
    validate_clustering_request,
    validate_groupings,
)
from app.openai_summarizer import DEFAULT_OPENAI_MODEL

NEWS_CLUSTERING_INSTRUCTIONS = """\
Group only articles describing substantially the same underlying news event or development, such
as the same launch, announcement, acquisition, regulation change, model release, or incident. Do
not group articles only because they share a broad category, industry, company, or technology; a
separate event stays separate. Use only the supplied article context and do not invent facts. Every
supplied article ID must appear exactly once, and unknown IDs must never be introduced. Produce
concise factual topic titles and summaries, briefly explain why articles belong together, and
return only the structured schema.
"""


class OpenAINewsClusterGrouping(BaseModel):
    """Typed schema for one provider grouping."""

    cluster_id: StrictInt = Field(ge=0)
    article_ids: list[StrictInt] = Field(min_length=1)
    topic_title: str = Field(min_length=1)
    topic_summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class OpenAINewsClusteringResponse(BaseModel):
    """Typed Responses API container for news groupings."""

    clusters: list[OpenAINewsClusterGrouping] = Field(min_length=1)


class ResponsesParser(Protocol):
    """Minimal typed Responses API surface used by the provider."""

    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[OpenAINewsClusteringResponse],
    ) -> Any:
        """Create and parse a structured model response."""


class OpenAIParsingClient(Protocol):
    """Minimal injectable OpenAI client surface used by the provider."""

    responses: ResponsesParser


class OpenAINewsClusterer:
    """Group same-event priority news with the OpenAI Responses API."""

    def __init__(
        self,
        *,
        client: OpenAIParsingClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL

    def cluster(
        self, sources: list[NewsClusterSource], *, topic_focus: str
    ) -> list[NewsClusterGrouping]:
        sources, focus = validate_clustering_request(
            sources, topic_focus, MAX_CLUSTER_ARTICLES
        )
        response = self.client.responses.parse(
            model=self.model,
            instructions=NEWS_CLUSTERING_INSTRUCTIONS,
            input=self._input_payload(sources, focus),
            text_format=OpenAINewsClusteringResponse,
        )
        parsed = self._parsed_output(response)
        groupings = [
            NewsClusterGrouping(
                cluster_id=cluster.cluster_id,
                article_ids=cluster.article_ids,
                topic_title=cluster.topic_title,
                topic_summary=cluster.topic_summary,
                reason=cluster.reason,
            )
            for cluster in parsed.clusters
        ]
        return validate_groupings(groupings, sources)

    @staticmethod
    def _input_payload(sources: list[NewsClusterSource], topic_focus: str) -> str:
        context = {
            "topic_focus": topic_focus,
            "priority_news": [
                {
                    "article_id": source.article_id,
                    "title": source.title,
                    "summary": source.summary,
                    "source": source.source,
                    "published_at": (
                        source.published_at.isoformat()
                        if source.published_at is not None
                        else None
                    ),
                    "importance_score": source.importance_score,
                    "relevance_score": source.relevance_score,
                    "priority_score": source.priority_score,
                }
                for source in sources
            ],
        }
        return json.dumps(context, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _parsed_output(response: Any) -> OpenAINewsClusteringResponse:
        for output_item in response.output:
            if getattr(output_item, "type", None) != "message":
                continue
            for content_item in output_item.content:
                parsed = getattr(content_item, "parsed", None)
                if parsed is not None:
                    return parsed
        raise ValueError("OpenAI response did not contain parsed news clusters")
