"""Database configuration and session management."""

import os
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ai_news.db")


class Base(DeclarativeBase):
    """Base class for database models."""


def create_db_engine(database_url: str = DATABASE_URL) -> Engine:
    """Create an engine configured for the supplied database URL."""

    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Provide a database session and close it after use."""

    with SessionLocal() as session:
        yield session


def init_db(db_engine: Engine = engine) -> None:
    """Create all registered database tables."""

    from app import models  # noqa: F401

    Base.metadata.create_all(bind=db_engine)
