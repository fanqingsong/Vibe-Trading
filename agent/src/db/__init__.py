"""Database layer: SQLAlchemy ORM engine, session factory, and ORM models.

Supports PostgreSQL (default for multi-user deployments) and SQLite (for local
single-user fallback via DATABASE_URL). The engine is created lazily on first
use so importing the package never fails when no DB is configured.
"""

from src.db.base import Base, get_engine, get_session_factory, get_session, init_db, is_db_enabled
from src.db.models import Setting, User

__all__ = [
    "Base",
    "Setting",
    "User",
    "get_engine",
    "get_session_factory",
    "get_session",
    "init_db",
    "is_db_enabled",
]
