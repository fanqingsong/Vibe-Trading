"""Regression tests for the settings API endpoints (DB-backed).

Settings now persist to the ``settings`` table via ``settings_store``. These
tests spin up an in-memory SQLite DB, exercise the loopback TestClient, and
assert secrets never leak into HTTP responses.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(db_session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_baostock_supported", lambda: False)
    monkeypatch.setattr(api_server, "_baostock_installed", lambda: False)
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_get_llm_settings_returns_defaults_when_empty(client: TestClient) -> None:
    response = client.get("/settings/llm")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["api_key_configured"] is False
    assert body["api_key_hint"] is None
    assert body["stored_in"] == "database"


def test_update_llm_settings_persists_to_database(client: TestClient) -> None:
    response = client.put(
        "/settings/llm",
        json={
            "provider": "openrouter",
            "model_name": "deepseek/deepseek-v4-pro",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "or-secret-value",
            "temperature": 0.1,
            "timeout_seconds": 45,
            "max_retries": 1,
            "reasoning_effort": "max",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openrouter"
    assert body["model_name"] == "deepseek/deepseek-v4-pro"
    assert body["api_key_configured"] is True
    assert body["api_key_hint"] is None
    assert "or-secret-value" not in response.text

    # A follow-up GET reads the persisted value back from the DB.
    follow_up = client.get("/settings/llm")
    assert follow_up.json()["provider"] == "openrouter"
    assert follow_up.json()["model_name"] == "deepseek/deepseek-v4-pro"
    assert follow_up.json()["api_key_configured"] is True


@pytest.mark.parametrize("placeholder", ["sk-xxx", "xxx", "gsk_xxx"])
def test_llm_settings_treat_documented_key_placeholders_as_unconfigured(
    client: TestClient, placeholder: str,
) -> None:
    response = client.put(
        "/settings/llm",
        json={
            "provider": "deepseek",
            "model_name": "deepseek-v4-pro",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": placeholder,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["api_key_configured"] is False
    assert body["api_key_hint"] is None
    assert placeholder not in response.text


def test_get_data_source_settings_defaults(client: TestClient) -> None:
    response = client.get("/settings/data-sources")

    assert response.status_code == 200
    body = response.json()
    assert body["tushare_token_configured"] is False
    assert body["tushare_token_hint"] is None
    assert body["baostock_supported"] is False
    assert body["stored_in"] == "database"


def test_update_data_source_settings_persists_tushare_token(client: TestClient) -> None:
    response = client.put(
        "/settings/data-sources",
        json={"tushare_token": "ts-secret-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tushare_token_configured"] is True
    assert body["tushare_token_hint"] is None
    assert "ts-secret-token" not in response.text

    follow_up = client.get("/settings/data-sources")
    assert follow_up.json()["tushare_token_configured"] is True


def test_settings_response_never_exposes_configured_secret_hints(
    client: TestClient,
) -> None:
    client.put(
        "/settings/llm",
        json={
            "provider": "openrouter",
            "model_name": "deepseek/deepseek-v4-pro",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "or-secret-private-value",
        },
    )
    client.put(
        "/settings/data-sources",
        json={"tushare_token": "ts-secret-private-token"},
    )

    llm_response = client.get("/settings/llm")
    data_response = client.get("/settings/data-sources")

    llm_body = llm_response.json()
    data_body = data_response.json()
    assert llm_body["api_key_configured"] is True
    assert llm_body["api_key_hint"] is None
    assert data_body["tushare_token_configured"] is True
    assert data_body["tushare_token_hint"] is None
    assert "or-secret-private-value" not in llm_response.text
    assert "ts-secret-private-token" not in data_response.text


def test_settings_reads_reject_remote_dev_mode_clients(
    db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    remote_client = TestClient(api_server.app, client=("203.0.113.10", 50000))

    llm_response = remote_client.get("/settings/llm")
    data_source_response = remote_client.get("/settings/data-sources")

    assert llm_response.status_code == 403
    assert data_source_response.status_code == 403


def test_settings_reads_allow_loopback_without_bearer_even_when_api_auth_key_configured(
    db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_server, "_baostock_supported", lambda: False)
    monkeypatch.setattr(api_server, "_baostock_installed", lambda: False)
    monkeypatch.setenv("API_AUTH_KEY", "settings-secret")
    local_client = TestClient(api_server.app, client=("127.0.0.1", 50000))

    unauthenticated_response = local_client.get("/settings/llm")
    authenticated_response = local_client.get(
        "/settings/llm",
        headers={"Authorization": "Bearer settings-secret"},
    )

    assert unauthenticated_response.status_code == 200
    assert authenticated_response.status_code == 200


def test_settings_writes_reject_remote_dev_mode_clients(
    db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    remote_client = TestClient(api_server.app, client=("203.0.113.10", 50000))

    response = remote_client.put(
        "/settings/data-sources",
        json={"tushare_token": "ts-secret-token"},
    )

    assert response.status_code == 403
