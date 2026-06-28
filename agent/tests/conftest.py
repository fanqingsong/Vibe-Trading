"""Shared fixtures and sys.path setup for all tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure agent/ is on sys.path so imports like `backtest.*` and `src.*` work.
AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@pytest.fixture
def db_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Spin up an isolated in-memory SQLite DB for settings/auth tests.

    Resets the cached SQLAlchemy engine/session-factory in ``src.db.base`` so
    each test gets a fresh schema. Yields ``None`` — tests interact via the
    store/API layer, not the session directly.
    """
    import src.db.base as base_module

    db_path = tmp_path / "test_auth.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Reset cached engine so the new DATABASE_URL takes effect.
    monkeypatch.setattr(base_module, "_engine", None)
    monkeypatch.setattr(base_module, "_SessionFactory", None)

    from src.db import init_db

    init_db()
    yield
    # Tear down: drop the cached engine so subsequent tests re-initialise.
    base_module._engine = None
    base_module._SessionFactory = None
