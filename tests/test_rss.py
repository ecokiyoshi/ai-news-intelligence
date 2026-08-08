from collections.abc import Callable

import feedparser
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import create_db_engine, init_db
from app.models import NewsArticle
from app.rss import CollectionResult, collect_feeds

VALID_FEED = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example News</title>
    <link>https://example.com</link>
    <description>Example feed</description>
    <item>
      <title>First article</title>
      <link>https://example.com/first</link>
      <pubDate>Fri, 08 Aug 2026 07:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Article without a date</title>
      <link>https://example.com/no-date</link>
    </item>
  </channel>
</rss>
"""

MISSING_FIELDS_FEED = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example News</title>
    <link>https://example.com</link>
    <description>Incomplete entries</description>
    <item><link>https://example.com/no-title</link></item>
    <item><title>No URL</title></item>
  </channel>
</rss>
"""

MISSING_SOURCE_FEED = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <link>https://example.com</link>
    <description>No source title</description>
    <item>
      <title>No source</title>
      <link>https://example.com/no-source</link>
    </item>
  </channel>
</rss>
"""


def local_parser(feed_data: bytes) -> Callable[[str], feedparser.FeedParserDict]:
    return lambda _url: feedparser.parse(feed_data)


def article_count(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(NewsArticle)) or 0


def test_valid_feed_is_normalized_and_stored(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'rss.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    result = collect_feeds(
        ["https://example.com/feed.xml"],
        session_factory=sessions,
        parser=local_parser(VALID_FEED),
    )

    assert result == CollectionResult(fetched=2, stored=2, skipped=0)
    with sessions() as session:
        articles = session.scalars(select(NewsArticle).order_by(NewsArticle.id)).all()
        assert [article.title for article in articles] == [
            "First article",
            "Article without a date",
        ]
        assert articles[0].source == "Example News"
        assert articles[0].published_at is not None
        assert articles[1].published_at is None
    engine.dispose()


def test_collecting_same_feed_twice_skips_duplicate_urls(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'duplicates.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    parser = local_parser(VALID_FEED)

    first_result = collect_feeds(["first"], session_factory=sessions, parser=parser)
    second_result = collect_feeds(["second"], session_factory=sessions, parser=parser)

    assert first_result.stored == 2
    assert second_result == CollectionResult(fetched=2, stored=0, skipped=2)
    assert article_count(sessions) == 2
    engine.dispose()


def test_duplicate_does_not_prevent_later_article_from_being_stored(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'continue.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        session.add(
            NewsArticle(
                title="Already stored",
                url="https://example.com/first",
                source="Example News",
            )
        )
        session.commit()

    result = collect_feeds(
        ["feed"], session_factory=sessions, parser=local_parser(VALID_FEED)
    )

    assert result == CollectionResult(fetched=2, stored=1, skipped=1)
    assert article_count(sessions) == 2
    engine.dispose()


def test_entries_missing_required_fields_are_skipped(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'invalid.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    feeds = {
        "missing-fields": feedparser.parse(MISSING_FIELDS_FEED),
        "missing-source": feedparser.parse(MISSING_SOURCE_FEED),
    }

    result = collect_feeds(
        feeds,
        session_factory=sessions,
        parser=lambda url: feeds[url],
    )

    assert result == CollectionResult(fetched=3, stored=0, skipped=3)
    assert article_count(sessions) == 0
    engine.dispose()


def test_feed_failures_do_not_abort_other_feeds(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'failures.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def parser(url: str):
        if url == "network-error":
            raise OSError("feed unavailable")
        if url == "parser-error":
            raise ValueError("parser failed")
        if url == "malformed":
            return feedparser.parse(b"not valid XML")
        return feedparser.parse(VALID_FEED)

    result = collect_feeds(
        ["network-error", "parser-error", "malformed", "valid"],
        session_factory=sessions,
        parser=parser,
    )

    assert result == CollectionResult(fetched=2, stored=2, skipped=0)
    assert article_count(sessions) == 2
    engine.dispose()
