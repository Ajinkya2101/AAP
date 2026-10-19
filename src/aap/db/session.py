"""Engine / session factory for the `aap` database."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from aap.config import get_settings


def sqlalchemy_url(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://"):]
    return dsn


@lru_cache
def get_engine(dsn: str | None = None) -> Engine:
    return create_engine(sqlalchemy_url(dsn or get_settings().aap_dsn), pool_pre_ping=True, pool_size=10,
                         max_overflow=10, future=True)


@lru_cache
def _factory(dsn: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(dsn), expire_on_commit=False)


@contextmanager
def session_scope(dsn: str | None = None) -> Iterator[Session]:
    session = _factory(dsn)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
