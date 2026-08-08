"""RSS and Atom feed collection."""

import calendar
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from time import struct_time
from typing import Any

import feedparser
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database import SessionLocal
from app.models import NewsArticle


@dataclass
class CollectionResult:
    """Counts produced by a feed collection run."""

    fetched: int = 0
    stored: int = 0
    skipped: int = 0


@dataclass
class NormalizedArticle:
    """A validated feed entry ready for persistence."""

    title: str
    url: str
    source: str
    published_at: datetime | None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _published_at(entry: Mapping[str, Any]) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not isinstance(parsed, struct_time):
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


def _normalize_entry(
    entry: Mapping[str, Any], feed: Mapping[str, Any]
) -> NormalizedArticle | None:
    entry_source = entry.get("source")
    source = _text(entry_source.get("title")) if isinstance(entry_source, Mapping) else None
    source = source or _text(feed.get("title"))
    title = _text(entry.get("title"))
    url = _text(entry.get("link"))

    if title is None or url is None or source is None:
        return None

    return NormalizedArticle(
        title=title,
        url=url,
        source=source,
        published_at=_published_at(entry),
    )


def collect_feeds(
    feed_urls: Iterable[str],
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
    parser: Callable[[str], Any] = feedparser.parse,
) -> CollectionResult:
    """Collect articles from one or more feeds without aborting on bad feeds or entries."""

    result = CollectionResult()

    with session_factory() as session:
        for feed_url in feed_urls:
            try:
                parsed_feed = parser(feed_url)
                entries = parsed_feed.get("entries", [])
                feed = parsed_feed.get("feed", {})
            except Exception:
                continue

            for entry in entries:
                result.fetched += 1
                article = _normalize_entry(entry, feed)
                if article is None:
                    result.skipped += 1
                    continue

                existing_id = session.scalar(
                    select(NewsArticle.id).where(NewsArticle.url == article.url)
                )
                if existing_id is not None:
                    result.skipped += 1
                    continue

                session.add(
                    NewsArticle(
                        title=article.title,
                        url=article.url,
                        source=article.source,
                        published_at=article.published_at,
                    )
                )
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    result.skipped += 1
                else:
                    result.stored += 1

    return result
