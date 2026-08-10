"""Claude-backed grouping provider for similar priority news."""

import json

from pydantic import BaseModel, Field, StrictInt

from app.anthropic_client import AnthropicClient, build_default_client, parse_structured, resolve_model
from app.news_clustering import (
    MAX_CLUSTER_ARTICLES,
    NewsClusterGrouping,
    NewsClusterSource,
    validate_clustering_request,
    validate_groupings,
)

NEWS_CLUSTERING_INSTRUCTIONS = """\
Group only articles describing substantially the same underlying news event or development, such
as the same launch, announcement, acquisition, regulation change, model release, or incident. Do
not group articles only because they share a broad category, industry, company, or technology; a
separate event stays separate. Use only the supplied article context and do not invent facts. Every
supplied article ID must appear exactly once, and unknown IDs must never be introduced. Produce
concise factual topic titles and summaries, briefly explain why articles belong together, and
return only the structured schema.
"""


class AnthropicNewsClusterGrouping(BaseModel):
    """Typed schema for one provider grouping."""

    cluster_id: StrictInt = Field(ge=0)
    article_ids: list[StrictInt] = Field(min_length=1)
    topic_title: str = Field(min_length=1)
    topic_summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AnthropicNewsClusteringResponse(BaseModel):
    """Typed structured-tool-call container for news groupings."""

    clusters: list[AnthropicNewsClusterGrouping] = Field(min_length=1)


class AnthropicNewsClusterer:
    """Group same-event priority news with the Anthropic Messages API."""

    def __init__(
        self,
        *,
        client: AnthropicClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else build_default_client(timeout)
        self.model = resolve_model(model)

    def cluster(
        self, sources: list[NewsClusterSource], *, topic_focus: str
    ) -> list[NewsClusterGrouping]:
        sources, focus = validate_clustering_request(
            sources, topic_focus, MAX_CLUSTER_ARTICLES
        )
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=NEWS_CLUSTERING_INSTRUCTIONS,
            input_text=self._input_payload(sources, focus),
            response_model=AnthropicNewsClusteringResponse,
        )
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
