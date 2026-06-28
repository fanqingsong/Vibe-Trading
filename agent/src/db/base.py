"""SQLAlchemy engine, declarative base, and session factory.

Configuration:
- ``DATABASE_URL`` env var selects the dialect. Examples:
  - PostgreSQL: ``postgresql+psycopg://user:pass@host:5432/dbname``
  - SQLite (dev fallback): ``sqlite:///~/.vibe-trading/auth.db``
- When ``DATABASE_URL`` is empty, the layer is inert: ``init_db`` is a no-op and
  ``get_session`` yields ``None``. This preserves the existing zero-config local
  dev experience (loopback trusted, no DB required).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

_DEFAULT_SQLITE_URL = "sqlite:///" + str(
    os.path.expanduser("~/.vibe-trading/auth.db")
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker] = None


def _resolve_database_url() -> str:
    """Return the configured database URL, or empty string when DB is disabled."""
    url = os.getenv("DATABASE_URL", "").strip()
    # When auth is disabled, callers may set DATABASE_URL=disabled to force inert mode.
    if url.lower() in {"disabled", "off", "none"}:
        return ""
    return url


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def get_engine() -> Optional[Engine]:
    """Lazily create and cache the SQLAlchemy engine.

    Returns ``None`` when no database is configured (inert dev mode).
    """
    global _engine
    if _engine is not None:
        return _engine

    url = _resolve_database_url()
    if not url:
        return None

    kwargs = {"future": True}
    if _is_sqlite(url):
        # Allow cross-thread access for SQLite (FastAPI threadpool runs sync routes).
        kwargs["connect_args"] = {"check_same_thread": False}

    _engine = create_engine(url, **kwargs)
    logger.info("Database engine created for %s", url.split("://")[0])
    return _engine


def get_session_factory() -> Optional[sessionmaker]:
    """Return a cached sessionmaker, or None in inert mode."""
    global _SessionFactory
    if _SessionFactory is not None:
        return _SessionFactory

    engine = get_engine()
    if engine is None:
        return None

    _SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session() -> Iterator[Optional[Session]]:
    """Context manager yielding a Session, or None in inert mode.

    Commits on clean exit, rolls back on exception, always closes.
    """
    factory = get_session_factory()
    if factory is None:
        yield None
        return

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> bool:
    """Create all tables. Returns True if a DB is configured, False otherwise.

    Safe to call at startup; no-op in inert mode.

    Also applies lightweight in-place schema upgrades (additive column additions)
    that ``Base.metadata.create_all`` cannot perform on pre-existing tables.
    See :func:`_apply_column_upgrades`.
    """
    engine = get_engine()
    if engine is None:
        return False

    # Import models so they register with Base.metadata before create_all.
    from src import db as _db  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_column_upgrades(engine)
    logger.info("Database tables ensured")
    return True


# Additive column upgrades applied on top of ``create_all``. ``create_all`` only
# creates missing tables — it will not add new columns to tables that already
# exist from an older deploy. Each entry is (table, column, SQL type decl); we
# inspect the live schema and ``ALTER TABLE ... ADD COLUMN`` when absent. This is
# intentionally limited to nullable / defaulted columns so it is safe for both
# SQLite and PostgreSQL. Remove an entry once all deployments have migrated.
_COLUMN_UPGRADES: tuple[tuple[str, str, str], ...] = (
    ("scheduled_tasks", "notify_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("scheduled_tasks", "notify_emails", "VARCHAR(512)"),
)


def _apply_column_upgrades(engine: Engine) -> None:
    """Add any missing columns declared in :data:`_COLUMN_UPGRADES`.

    Idempotent: inspects each table's existing columns and only issues
    ``ALTER TABLE ... ADD COLUMN`` for the missing ones. Failures are logged at
    warning level and never raised — a partially-upgraded schema still boots so
    the operator can fix the DB without the app refusing to start.
    """
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
    except Exception:  # noqa: BLE001 — inspection must never block startup
        logger.warning("schema inspection failed; skipping column upgrades", exc_info=True)
        return

    for table, column, type_decl in _COLUMN_UPGRADES:
        if not insp.has_table(table):
            continue
        try:
            existing = {c["name"] for c in insp.get_columns(table)}
        except Exception:  # noqa: BLE001
            logger.warning("get_columns(%s) failed; skipping", table, exc_info=True)
            continue
        if column in existing:
            continue
        stmt = f'ALTER TABLE "{table}" ADD COLUMN "{column}" {type_decl}'
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            logger.info("column upgrade: %s.%s added", table, column)
        except Exception:  # noqa: BLE001 — one failed column must not abort the rest
            logger.warning("column upgrade %s.%s failed", table, column, exc_info=True)


def is_db_enabled() -> bool:
    """Return whether a real database backend is configured."""
    return get_engine() is not None
