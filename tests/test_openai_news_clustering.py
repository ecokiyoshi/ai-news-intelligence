from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast

import pytest

from app.news_clustering import NewsClusterSource, NewsClusterer
from app.openai_news_clustering import (
    OpenAINewsClusterGrouping,
    OpenAINewsClusteringResponse,
    OpenAINewsClusterer,
)


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


def parsed_group(cluster_id: int, article_ids: list[int]) -> OpenAINewsClusterGrouping:
    return OpenAINewsClusterGrouping(
        cluster_id=cluster_id,
        article_ids=article_ids,
        topic_title="DJI launch",
        topic_summary="DJI announced a new drone.",
        reason="The articles cover the same launch.",
    )


class FakeResponses:
    def __init__(self, parsed=None, error: Exception | None = None) -> None:
        self.parsed = parsed
        self.error = error
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", parsed=self.parsed)],
                )
            ]
        )


class FakeClient:
    def __init__(self, parsed=None, error: Exception | None = None) -> None:
        self.responses = FakeResponses(parsed, error)


def test_openai_clusterer_uses_typed_responses_and_compact_context() -> None:
    sources = [source(1, "DJI launches Drone X"), source(2, "Drone X announced")]
    client = FakeClient(
        OpenAINewsClusteringResponse(clusters=[parsed_group(0, [1, 2])])
    )
    clusterer: NewsClusterer = cast(
        NewsClusterer, OpenAINewsClusterer(client=client, model="test-model")
    )

    groupings = clusterer.cluster(sources, topic_focus="drone technology")

    assert groupings[0].article_ids == [1, 2]
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["text_format"] is OpenAINewsClusteringResponse
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
        assert expected in call["input"]


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
def test_openai_clusterer_rejects_malformed_provider_output(clusters) -> None:
    parsed = SimpleNamespace(clusters=clusters)
    with pytest.raises(ValueError):
        OpenAINewsClusterer(client=FakeClient(parsed)).cluster(
            [source(1, "One"), source(2, "Two")], topic_focus="drone news"
        )


def test_openai_clusterer_rejects_missing_parsed_output() -> None:
    with pytest.raises(ValueError, match="parsed news clusters"):
        OpenAINewsClusterer(client=FakeClient()).cluster(
            [source(1, "One")], topic_focus="drone news"
        )


def test_openai_clusterer_propagates_provider_exception() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        OpenAINewsClusterer(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).cluster([source(1, "One")], topic_focus="drone news")
