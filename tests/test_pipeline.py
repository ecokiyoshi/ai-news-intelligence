from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import create_db_engine, init_db
from app.models import NewsArticle
from app.pipeline import MetadataTextProvider, PipelineResult, run_pipeline
from app.scoring import ScoreResult
from app.summarization import SummaryResult


def feed(*titles: str, source: str = "Example News") -> dict:
    return {
        "feed": {"title": source},
        "entries": [
            {"title": title, "link": f"https://example.com/{title.replace(' ', '-')}"}
            for title in titles
        ],
    }


class CountingSummarizer:
    def __init__(self, failing_text: str | None = None) -> None:
        self.calls: list[str] = []
        self.failing_text = failing_text

    def summarize(self, text: str) -> SummaryResult:
        self.calls.append(text)
        if self.failing_text and self.failing_text in text:
            raise RuntimeError("summarization failed")
        return SummaryResult(summary=f"Summary: {text}")


class CountingScorer:
    def __init__(self, failing_text: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failing_text = failing_text

    def score(self, text: str, relevance_target: str) -> ScoreResult:
        self.calls.append((text, relevance_target))
        if self.failing_text and self.failing_text in text:
            raise RuntimeError("scoring failed")
        score = {
            "Highest": 95,
            "High": 90,
            "High B": 90,
            "Medium": 80,
            "Low": 60,
        }.get(text.splitlines()[0], 75)
        return ScoreResult(score, score, "Deterministic test score.")


@pytest.fixture
def pipeline_db(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'pipeline.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield engine, sessions
    engine.dispose()


def run_local(session: Session, feeds: dict[str, dict], **kwargs) -> PipelineResult:
    return run_pipeline(
        feeds.keys(),
        "AI industry",
        kwargs.pop("summarizer", CountingSummarizer()),
        kwargs.pop("scorer", CountingScorer()),
        kwargs.pop("text_provider", MetadataTextProvider()),
        session,
        feed_parser=lambda url: feeds[url],
        **kwargs,
    )


def test_complete_pipeline_processes_multiple_feeds(pipeline_db) -> None:
    _, sessions = pipeline_db
    feeds = {"one": feed("First"), "two": feed("Second", source="Other News")}
    with sessions() as session:
        result = run_local(session, feeds)
        articles = list(session.scalars(select(NewsArticle).order_by(NewsArticle.id)))

    assert result.feeds_requested == 2
    assert (result.articles_fetched, result.articles_stored, result.articles_skipped) == (2, 2, 0)
    assert (result.articles_summarized, result.articles_scored, result.articles_failed) == (2, 2, 0)
    assert len(result.priority_articles) == 2
    assert all(item.summary and item.score_reason for item in articles)
    assert all(item.summarized_at and item.scored_at for item in articles)


def test_repeated_run_skips_duplicates_and_provider_calls(pipeline_db) -> None:
    _, sessions = pipeline_db
    feeds = {"feed": feed("First", "Second")}
    summarizer = CountingSummarizer()
    scorer = CountingScorer()
    with sessions() as session:
        first = run_local(session, feeds, summarizer=summarizer, scorer=scorer)
        second = run_local(session, feeds, summarizer=summarizer, scorer=scorer)

    assert (first.articles_stored, first.articles_summarized, first.articles_scored) == (2, 2, 2)
    assert (second.articles_stored, second.articles_skipped) == (0, 2)
    assert (second.articles_summarized, second.articles_scored) == (0, 0)
    assert len(summarizer.calls) == len(scorer.calls) == 2


def test_existing_processing_is_skipped_and_force_flags_reprocess(pipeline_db) -> None:
    _, sessions = pipeline_db
    summarizer = CountingSummarizer()
    scorer = CountingScorer()
    with sessions() as session:
        article = NewsArticle(
            title="Existing",
            url="https://example.com/existing",
            source="Example News",
            summary="Existing summary",
            summarized_at=datetime.now(timezone.utc),
            importance_score=50,
            relevance_score=60,
            score_reason="Existing score",
            scored_at=datetime.now(timezone.utc),
        )
        session.add(article)
        session.commit()

        skipped = run_local(session, {"empty": feed()}, summarizer=summarizer, scorer=scorer)
        forced = run_local(
            session,
            {"empty": feed()},
            summarizer=summarizer,
            scorer=scorer,
            force_resummarize=True,
            force_rescore=True,
        )

    assert (skipped.articles_summarized, skipped.articles_scored) == (0, 0)
    assert (forced.articles_summarized, forced.articles_scored) == (1, 1)
    assert len(summarizer.calls) == len(scorer.calls) == 1


def test_summarization_failure_isolated_from_other_articles(pipeline_db) -> None:
    _, sessions = pipeline_db
    summarizer = CountingSummarizer(failing_text="Bad summary")
    scorer = CountingScorer()
    with sessions() as session:
        result = run_local(
            session,
            {"feed": feed("Bad summary", "Good article")},
            summarizer=summarizer,
            scorer=scorer,
        )
        assert session.scalar(select(NewsArticle.id).limit(1)) is not None

    assert (result.articles_summarized, result.articles_scored, result.articles_failed) == (1, 1, 1)
    assert len(result.priority_articles) == 1


def test_scoring_failure_isolated_from_other_articles(pipeline_db) -> None:
    _, sessions = pipeline_db
    with sessions() as session:
        result = run_local(
            session,
            {"feed": feed("Bad scoring", "Good article")},
            scorer=CountingScorer(failing_text="Bad scoring"),
        )
        assert session.scalar(select(NewsArticle.id).limit(1)) is not None

    assert (result.articles_summarized, result.articles_scored, result.articles_failed) == (2, 1, 1)
    assert len(result.priority_articles) == 1


def test_text_provider_failure_isolated(pipeline_db) -> None:
    class FailingTextProvider:
        def get_text(self, article: NewsArticle) -> str:
            if article.title == "Bad text":
                raise RuntimeError("text unavailable")
            return article.title

    _, sessions = pipeline_db
    with sessions() as session:
        result = run_local(
            session,
            {"feed": feed("Bad text", "Good article")},
            text_provider=FailingTextProvider(),
        )
    assert (result.articles_summarized, result.articles_scored, result.articles_failed) == (1, 1, 1)


def test_ranking_options_apply_to_successful_articles(pipeline_db) -> None:
    _, sessions = pipeline_db
    feeds = {
        "a": feed("Highest", "High", "Medium", source="Source A"),
        "b": feed("High B", source="Source B"),
        "c": feed("Low", source="Source C"),
    }
    with sessions() as session:
        result = run_local(
            session,
            feeds,
            limit=3,
            minimum_priority_score=80,
            max_per_source=1,
        )
        sources = {
            item.id: item.source for item in session.scalars(select(NewsArticle))
        }
    assert len(result.priority_articles) == 2
    assert [sources[item.article_id] for item in result.priority_articles] == ["Source A", "Source B"]
    assert all(item.priority_score >= 80 for item in result.priority_articles)


@pytest.mark.parametrize(
    "overrides",
    [
        {"limit": 0},
        {"minimum_priority_score": 101},
        {"max_per_source": 0},
        {"importance_weight": 0.5, "relevance_weight": 0.4},
        {"force_resummarize": 1},
        {"force_rescore": 0},
    ],
)
def test_invalid_configuration_fails_before_collection_or_providers(pipeline_db, overrides) -> None:
    _, sessions = pipeline_db
    parser_calls: list[str] = []
    summarizer = CountingSummarizer()
    scorer = CountingScorer()
    with sessions() as session, pytest.raises(ValueError):
        run_pipeline(
            ["feed"],
            "AI industry",
            summarizer,
            scorer,
            MetadataTextProvider(),
            session,
            feed_parser=lambda url: parser_calls.append(url),
            **overrides,
        )
    assert parser_calls == []
    assert summarizer.calls == []
    assert scorer.calls == []


@pytest.mark.parametrize("target", ["", " \n\t"])
def test_empty_target_fails_before_provider_work(pipeline_db, target: str) -> None:
    _, sessions = pipeline_db
    parser_calls: list[str] = []
    with sessions() as session, pytest.raises(ValueError):
        run_pipeline(
            ["feed"],
            target,
            CountingSummarizer(),
            CountingScorer(),
            MetadataTextProvider(),
            session,
            feed_parser=lambda url: parser_calls.append(url),
        )
    assert parser_calls == []


@pytest.mark.parametrize("urls", [[], [""], "single-url"])
def test_invalid_feed_urls_fail_fast(pipeline_db, urls) -> None:
    _, sessions = pipeline_db
    with sessions() as session, pytest.raises(ValueError):
        run_pipeline(
            urls,
            "AI industry",
            CountingSummarizer(),
            CountingScorer(),
            MetadataTextProvider(),
            session,
        )
