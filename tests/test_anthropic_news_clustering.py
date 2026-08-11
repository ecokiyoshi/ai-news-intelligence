from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.anthropic_news_clustering import (
    AnthropicNewsClusterer,
    AnthropicNewsClusterGrouping,
    AnthropicNewsClusteringResponse,
)
from app.news_clustering import NewsClusterer, NewsClusterSource
from support_anthropic import FakeClient, NoToolCallClient, call_input_text


def source(article_id: int, title: str) -> NewsClusterSource:
    return NewsClusterSource(
        article_id=article_id,
        title=title,
        summary=f"Summary for {title}",
        source="Example News",
        published_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        importance_score=90,
        relevance_score=85,
        priority_score=88.0,
    )


def parsed_group(cluster_id: int, article_ids: list[int]) -> AnthropicNewsClusterGrouping:
    return AnthropicNewsClusterGrouping(
        cluster_id=cluster_id,
        article_ids=article_ids,
        topic_title="DJI launch",
        topic_summary="DJI announced a new drone.",
        reason="The articles cover the same launch.",
    )


def test_anthropic_clusterer_uses_structured_tool_call_and_compact_context() -> None:
    sources = [source(1, "DJI launches Drone X"), source(2, "Drone X announced")]
    client = FakeClient(
        AnthropicNewsClusteringResponse(clusters=[parsed_group(0, [1, 2])])
    )
    clusterer: NewsClusterer = AnthropicNewsClusterer(client=client, model="test-model")

    groupings = clusterer.cluster(sources, topic_focus="drone technology")

    assert groupings[0].article_ids == [1, 2]
    call = client.messages.calls[0]
    assert call["model"] == "test-model"
    text = call_input_text(call)
    for expected in (
        "drone technology",
        '"article_id":1',
        "DJI launches Drone X",
        "Summary for DJI launches Drone X",
        "Example News",
        '"importance_score":90',
        '"relevance_score":85',
        '"priority_score":88.0',
    ):
        assert expected in text


@pytest.mark.parametrize(
    "clusters",
    [
        [],
        [parsed_group(0, [1])],
        [parsed_group(0, [1, 2]), parsed_group(1, [2])],
        [parsed_group(0, [1, 2, 99])],
        [parsed_group(0, [1]), parsed_group(0, [2])],
        [SimpleNamespace(cluster_id=0, article_ids=[1, 2], topic_title=" ",
                         topic_summary="Summary", reason="Reason")],
    ],
)
def test_anthropic_clusterer_rejects_malformed_provider_output(clusters) -> None:
    parsed = SimpleNamespace(clusters=clusters)
    with pytest.raises(ValueError):
        AnthropicNewsClusterer(client=FakeClient(parsed)).cluster(
            [source(1, "One"), source(2, "Two")], topic_focus="drone news"
        )


def test_anthropic_clusterer_rejects_missing_tool_call_output() -> None:
    with pytest.raises(ValueError, match="structured tool call"):
        AnthropicNewsClusterer(client=NoToolCallClient()).cluster(
            [source(1, "One")], topic_focus="drone news"
        )


def test_anthropic_clusterer_propagates_provider_exception() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        AnthropicNewsClusterer(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).cluster([source(1, "One")], topic_focus="drone news")
