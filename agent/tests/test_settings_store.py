"""Unit tests for the DB-backed settings store (``src.db.settings_store``).

Covers CRUD upsert semantics, secret flagging, the one-time env seed, and the
inert no-DB fallback path.
"""

from __future__ import annotations

import os

from src.db import settings_store as store


# --------------------------------------------------------------------------- #
# Read / Write
# --------------------------------------------------------------------------- #


def test_get_settings_empty_category_returns_empty_dict(db_session) -> None:
    assert store.get_settings("llm") == {}


def test_upsert_then_get_roundtrip(db_session) -> None:
    store.upsert_settings("llm", {"LLM_PROVIDER": "openrouter", "LLM_TEMPERATURE": "0.3"})
    result = store.get_settings("llm")
    assert result["LLM_PROVIDER"] == "openrouter"
    assert result["LLM_TEMPERATURE"] == "0.3"


def test_upsert_updates_existing_row_in_place(db_session) -> None:
    store.upsert_settings("llm", {"LLM_PROVIDER": "openai"})
    store.upsert_settings("llm", {"LLM_PROVIDER": "deepseek"})
    assert store.get_settings("llm") == {"LLM_PROVIDER": "deepseek"}


def test_upsert_empty_value_deletes_row(db_session) -> None:
    store.upsert_settings("llm", {"LLM_PROVIDER": "openai", "LLM_MODEL_NAME": "gpt-4"})
    store.upsert_settings("llm", {"LLM_MODEL_NAME": ""})
    result = store.get_settings("llm")
    assert "LLM_MODEL_NAME" not in result
    assert result["LLM_PROVIDER"] == "openai"


def test_get_setting_single_value_with_default(db_session) -> None:
    assert store.get_setting("llm", "MISSING_KEY", "fallback") == "fallback"
    store.upsert_settings("llm", {"LLM_PROVIDER": "groq"})
    assert store.get_setting("llm", "LLM_PROVIDER") == "groq"


def test_categories_are_isolated(db_session) -> None:
    store.upsert_settings("llm", {"LLM_PROVIDER": "openai"})
    store.upsert_settings("email", {"SMTP_HOST": "smtp.qq.com"})
    assert store.get_settings("llm") == {"LLM_PROVIDER": "openai"}
    assert store.get_settings("email") == {"SMTP_HOST": "smtp.qq.com"}


# --------------------------------------------------------------------------- #
# Secret flagging
# --------------------------------------------------------------------------- #


def test_secret_keys_flagged_on_insert(db_session) -> None:
    store.upsert_settings(
        "llm",
        {"OPENAI_API_KEY": "sk-secret", "LLM_PROVIDER": "openai"},
        secret_keys={"OPENAI_API_KEY"},
    )
    from sqlalchemy import select

    from src.db.base import get_session
    from src.db.models import Setting

    with get_session() as session:
        rows = {r.key: r.is_secret for r in session.execute(select(Setting).where(Setting.category == "llm")).scalars()}
    assert rows["OPENAI_API_KEY"] is True
    assert rows["LLM_PROVIDER"] is False


def test_secret_flag_updated_on_overwrite(db_session) -> None:
    store.upsert_settings("email", {"SMTP_PASSWORD": "pw"})
    store.upsert_settings("email", {"SMTP_PASSWORD": "new-pw"}, secret_keys={"SMTP_PASSWORD"})

    from sqlalchemy import select

    from src.db.base import get_session
    from src.db.models import Setting

    with get_session() as session:
        row = session.execute(
            select(Setting).where(Setting.category == "email", Setting.key == "SMTP_PASSWORD")
        ).scalar_one()
    assert row.is_secret is True


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #


def test_seed_imports_from_env_when_empty(db_session, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "zhipu")
    monkeypatch.setenv("LLM_MODEL_NAME", "glm-5.1")
    monkeypatch.setenv("ZHIPU_API_KEY", "fake-key")

    seeded = store.seed_settings_from_env_if_empty("llm")
    assert seeded is True
    result = store.get_settings("llm")
    assert result["LLM_PROVIDER"] == "zhipu"
    assert result["ZHIPU_API_KEY"] == "fake-key"


def test_seed_is_idempotent_when_rows_exist(db_session, monkeypatch) -> None:
    store.upsert_settings("llm", {"LLM_PROVIDER": "openai"})
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")

    seeded = store.seed_settings_from_env_if_empty("llm")
    assert seeded is False
    # Original value preserved — seed did not overwrite.
    assert store.get_settings("llm") == {"LLM_PROVIDER": "openai"}


def test_seed_noop_when_env_keys_absent(db_session) -> None:
    # No env vars set for this category.
    seeded = store.seed_settings_from_env_if_empty("data_source")
    assert seeded is False


def test_seed_all_categories(db_session, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("SMTP_HOST", "smtp.qq.com")
    monkeypatch.setenv("TUSHARE_TOKEN", "ts-token")

    result = store.seed_all_categories_if_empty()
    assert result["llm"] is True
    assert result["email"] is True
    assert result["data_source"] is True


# --------------------------------------------------------------------------- #
# Runtime env sync
# --------------------------------------------------------------------------- #


def test_sync_db_settings_to_runtime_env_populates_os_environ(db_session, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    store.upsert_settings("llm", {"LLM_PROVIDER": "moonshot"})
    store.upsert_settings("email", {"SMTP_HOST": "smtp.gmail.com"})

    store.sync_db_settings_to_runtime_env()
    assert os.environ["LLM_PROVIDER"] == "moonshot"
    assert os.environ["SMTP_HOST"] == "smtp.gmail.com"


def test_sync_does_not_touch_environ_for_missing_rows(db_session, monkeypatch) -> None:
    """sync loads DB rows into os.environ but does not clear keys absent from the DB.

    Clearing stale env vars is the responsibility of the API update handlers
    via ``_sync_runtime_env``; the startup sync is additive only.
    """
    monkeypatch.setenv("LLM_PROVIDER", "pre-existing")
    # DB has no rows for 'llm'.
    store.sync_db_settings_to_runtime_env()
    # Pre-existing env var is untouched (sync is additive, not destructive).
    assert os.environ["LLM_PROVIDER"] == "pre-existing"


# --------------------------------------------------------------------------- #
# Inert (no-DB) fallback
# --------------------------------------------------------------------------- #


def test_inert_mode_reads_from_os_environ(monkeypatch, tmp_path) -> None:
    import src.db.base as base_module

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(base_module, "_engine", None)
    monkeypatch.setattr(base_module, "_SessionFactory", None)

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    result = store.get_settings("llm")
    assert result.get("LLM_PROVIDER") == "gemini"


def test_inert_mode_upsert_is_noop(monkeypatch, tmp_path) -> None:
    import src.db.base as base_module

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(base_module, "_engine", None)
    monkeypatch.setattr(base_module, "_SessionFactory", None)

    # Should not raise; persistence is skipped in inert mode.
    store.upsert_settings("llm", {"LLM_PROVIDER": "openai"})
