import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import create_db_engine, init_db
from app.models import NewsArticle


@pytest.fixture
def db_engine(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    yield engine
    engine.dispose()


def test_init_db_creates_news_articles_table(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'init.db'}")

    init_db(engine)

    assert inspect(engine).has_table("news_articles")
    engine.dispose()


def test_news_article_can_be_created_and_read(db_engine) -> None:
    with Session(db_engine) as session:
        article = NewsArticle(
            title="AI news",
            url="https://example.com/ai-news",
            source="Example News",
        )
        session.add(article)
        session.commit()

        saved_article = session.scalar(
            select(NewsArticle).where(NewsArticle.url == "https://example.com/ai-news")
        )

        assert saved_article is not None
        assert saved_article.title == "AI news"
        assert saved_article.source == "Example News"
        assert saved_article.published_at is None
        assert saved_article.created_at is not None


def test_duplicate_url_is_rejected_by_database(db_engine) -> None:
    with Session(db_engine) as session:
        session.add_all(
            [
                NewsArticle(title="First", url="https://example.com/shared", source="Source A"),
                NewsArticle(title="Second", url="https://example.com/shared", source="Source B"),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()
