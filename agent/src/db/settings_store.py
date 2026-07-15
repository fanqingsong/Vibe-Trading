"""Persistence layer for global application settings (LLM / data-source / email).

Settings live in the ``settings`` table as ``(category, key, value)`` triples.
When no database is configured (inert dev / loopback mode), reads fall back to
``os.environ`` so the rest of the system keeps working without a DB. Writes are
only persisted when a DB is present; in inert mode they at least update the
live process environment via the caller's ``_sync_runtime_env`` step.

A one-time seed imports existing values from ``agent/.env`` the first time a
category is read from an empty table, so upgrading an existing deployment
preserves its configured credentials.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.db.base import get_session, is_db_enabled
from src.db.models import Setting

logger = logging.getLogger(__name__)

# Canonical category names. These map 1:1 to the three settings sections shown
# in the Web UI.
CATEGORY_LLM = "llm"
CATEGORY_DATA_SOURCE = "data_source"
CATEGORY_EMAIL = "email"


def _is_db_active() -> bool:
    """Return True when a real SQLAlchemy backend is configured."""
    return is_db_enabled()


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


def get_settings(category: str) -> dict[str, str]:
    """Return all key=value pairs for ``category``.

    When no DB is configured, falls back to scanning ``os.environ`` for the
    keys registered via :func:`seed_settings_from_env_if_empty` mappings. This
    keeps inert dev mode functional.
    """
    if not _is_db_active():
        return _read_category_from_env(category)
    try:
        with get_session() as session:
            if session is None:
                return _read_category_from_env(category)
            stmt = select(Setting).where(Setting.category == category)
            rows = session.execute(stmt).scalars().all()
            if not rows:
                return {}
            return {row.key: row.value for row in rows}
    except SQLAlchemyError:
        logger.exception("get_settings(%s) failed; falling back to env", category)
        return _read_category_from_env(category)


def get_setting(category: str, key: str, default: str = "") -> str:
    """Return a single value, or ``default`` if unset."""
    if not _is_db_active():
        return os.environ.get(key, default)
    try:
        with get_session() as session:
            if session is None:
                return os.environ.get(key, default)
            stmt = select(Setting).where(
                Setting.category == category,
                Setting.key == key,
            )
            row = session.execute(stmt).scalar_one_or_none()
            return row.value if row is not None else default
    except SQLAlchemyError:
        logger.exception("get_setting(%s, %s) failed", category, key)
        return os.environ.get(key, default)


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #


def upsert_settings(
    category: str,
    updates: Mapping[str, str],
    secret_keys: Iterable[str] = (),
) -> None:
    """Upsert key-value pairs for ``category``.

    A blank value deletes the row (matching the previous dotenv semantics where
    an empty value meant "unset"). ``secret_keys`` marks which keys are
    sensitive so the ``is_secret`` flag is preserved for masking in API
    responses.

    No-DB (inert) mode is a no-op on the persistence side; the caller is
    expected to have already applied the values to ``os.environ`` via
    ``_sync_runtime_env``.
    """
    if not _is_db_active():
        return
    secret_set = {k for k in secret_keys}
    try:
        with get_session() as session:
            if session is None:
                return
            for key, value in updates.items():
                stmt = select(Setting).where(
                    Setting.category == category,
                    Setting.key == key,
                )
                existing = session.execute(stmt).scalar_one_or_none()
                if value == "":
                    if existing is not None:
                        session.delete(existing)
                elif existing is not None:
                    existing.value = value
                    existing.is_secret = key in secret_set
                else:
                    session.add(
                        Setting(
                            category=category,
                            key=key,
                            value=value,
                            is_secret=key in secret_set,
                        )
                    )
    except SQLAlchemyError:
        logger.exception("upsert_settings(%s) failed", category)
        raise


# --------------------------------------------------------------------------- #
# Seed (one-time import from agent/.env)
# --------------------------------------------------------------------------- #

# Maps each category to the dotenv keys that belong to it. Used by the seed
# step and by the inert-mode env fallback.
CATEGORY_ENV_KEYS: dict[str, tuple[str, ...]] = {
    CATEGORY_LLM: (
        "LLM_PROVIDER",
        "LLM_MODEL_NAME",
        "LLM_TEMPERATURE",
        "LLM_REASONING_EFFORT",
        "TIMEOUT_SECONDS",
        "MAX_RETRIES",
        # Provider-specific API keys / base URLs (only the active provider's
        # keys are meaningful at runtime, but all are seeded so switching
        # providers in the UI keeps the previously entered key).
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "GEMINI_API_KEY",
        "GEMINI_BASE_URL",
        "GROQ_API_KEY",
        "GROQ_BASE_URL",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "ZHIPU_API_KEY",
        "ZHIPU_BASE_URL",
        "MOONSHOT_API_KEY",
        "MOONSHOT_BASE_URL",
        "MINIMAX_API_KEY",
        "MINIMAX_BASE_URL",
        "MIMO_API_KEY",
        "MIMO_BASE_URL",
        "ZAI_API_KEY",
        "ZAI_BASE_URL",
        "OLLAMA_BASE_URL",
    ),
    CATEGORY_DATA_SOURCE: (
        "TUSHARE_TOKEN",
        "CCXT_EXCHANGE",
        "FUTU_HOST",
        "FUTU_PORT",
    ),
    CATEGORY_EMAIL: (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "SMTP_USE_TLS",
        "NOTIFY_RECIPIENTS",
        "NOTIFY_TRADE_ALERTS",
        "NOTIFY_REPORTS",
    ),
}

# Keys whose values are secrets and must be masked in API responses.
SECRET_KEYS: dict[str, frozenset[str]] = {
    CATEGORY_LLM: frozenset(
        {
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "DASHSCOPE_API_KEY",
            "ZHIPU_API_KEY",
            "MOONSHOT_API_KEY",
            "MINIMAX_API_KEY",
            "MIMO_API_KEY",
            "ZAI_API_KEY",
        }
    ),
    CATEGORY_DATA_SOURCE: frozenset({"TUSHARE_TOKEN"}),
    CATEGORY_EMAIL: frozenset({"SMTP_PASSWORD"}),
}


def _read_category_from_env(category: str) -> dict[str, str]:
    """Read a category's keys from os.environ (inert-mode fallback)."""
    keys = CATEGORY_ENV_KEYS.get(category, ())
    return {k: os.environ[k] for k in keys if os.environ.get(k)}


def seed_settings_from_env_if_empty(category: str, env_source: Mapping[str, str] | None = None) -> bool:
    """Import a category's keys from the environment into the DB, once.

    Runs only when the DB table has zero rows for ``category``. Subsequent
    calls are no-ops, so the database is the sole source of truth after the
    first boot.

    Args:
        category: The settings category to seed.
        env_source: Optional explicit environment mapping (defaults to
            ``os.environ``). Tests pass a dict; production reads the live
            process environment.

    Returns:
        True if seeding happened, False if the DB was inert, the category
        already had rows, or no keys were present in the environment.
    """
    if not _is_db_active():
        return False
    source = env_source if env_source is not None else os.environ
    keys = CATEGORY_ENV_KEYS.get(category, ())
    secrets = SECRET_KEYS.get(category, frozenset())
    try:
        with get_session() as session:
            if session is None:
                return False
            existing = session.execute(
                select(Setting).where(Setting.category == category)
            ).scalars().all()
            if existing:
                return False
            seeded = 0
            for key in keys:
                value = source.get(key, "")
                if value:
                    session.add(
                        Setting(
                            category=category,
                            key=key,
                            value=value,
                            is_secret=key in secrets,
                        )
                    )
                    seeded += 1
            return seeded > 0
    except SQLAlchemyError:
        logger.exception("seed_settings_from_env_if_empty(%s) failed", category)
        return False


def seed_all_categories_if_empty(env_source: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Seed every known category. Returns a dict of category -> seeded?."""
    return {
        category: seed_settings_from_env_if_empty(category, env_source=env_source)
        for category in CATEGORY_ENV_KEYS
    }


# --------------------------------------------------------------------------- #
# Runtime env sync (DB -> os.environ)
# --------------------------------------------------------------------------- #


#: Legacy dotenv / DB keys from pre-rename installs. Mapped onto the current
#: ``LLM_*`` names when the modern key is absent so old deployments keep
#: working after the LANGCHAIN_* → LLM_* rename.
_LEGACY_LLM_KEY_ALIASES: tuple[tuple[str, str], ...] = (
    ("LANGCHAIN_PROVIDER", "LLM_PROVIDER"),
    ("LANGCHAIN_MODEL_NAME", "LLM_MODEL_NAME"),
    ("LANGCHAIN_TEMPERATURE", "LLM_TEMPERATURE"),
)


def sync_db_settings_to_runtime_env() -> None:
    """Load all settings from the DB into ``os.environ`` at startup.

    Every existing ``os.getenv('LLM_PROVIDER')`` /
    ``os.getenv('SMTP_HOST')`` call site keeps working unchanged — they still
    read ``os.environ``, but the source of truth is now the database. No-op in
    inert (no-DB) mode.

    Also projects legacy ``LANGCHAIN_*`` rows onto ``LLM_*`` when the modern
    key is missing (one-time rename compatibility).
    """
    if not _is_db_active():
        return
    try:
        with get_session() as session:
            if session is None:
                return
            rows = session.execute(select(Setting)).scalars().all()
            for row in rows:
                if row.value:
                    os.environ[row.key] = row.value
                else:
                    os.environ.pop(row.key, None)
            # Legacy alias: prefer the modern key if both are present.
            for legacy, modern in _LEGACY_LLM_KEY_ALIASES:
                if not os.environ.get(modern) and os.environ.get(legacy):
                    os.environ[modern] = os.environ[legacy]
    except SQLAlchemyError:
        logger.exception("sync_db_settings_to_runtime_env failed")
