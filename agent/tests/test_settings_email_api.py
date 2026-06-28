"""Email / SMTP settings API tests (DB-backed).

Mirrors test_settings_api.py: an in-memory SQLite DB isolates state, the
loopback TestClient is used, and every test asserts the SMTP password never
appears in the response body.
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


def test_get_email_settings_defaults_to_unconfigured(client: TestClient) -> None:
    response = client.get("/settings/email")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["password_configured"] is False
    assert body["host"] == ""
    assert body["recipients"] == []
    assert body["stored_in"] == "database"


def test_update_email_settings_persists_smtp_config(client: TestClient) -> None:
    response = client.put(
        "/settings/email",
        json={
            "host": "smtp.qq.com",
            "port": 465,
            "user": "alerts@example.com",
            "password": "real-smtp-secret",
            "use_tls": True,
            "from_addr": "alerts@example.com",
            "recipients": ["ops@example.com", "risk@example.com"],
            "notify_trade_alerts": True,
            "notify_reports": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["host"] == "smtp.qq.com"
    assert body["port"] == 465
    assert body["user"] == "alerts@example.com"
    assert body["password_configured"] is True
    assert body["use_tls"] is True
    assert body["notify_trade_alerts"] is True
    assert body["notify_reports"] is False
    assert body["recipients"] == ["ops@example.com", "risk@example.com"]
    assert "real-smtp-secret" not in response.text

    # Follow-up GET reads persisted values from the DB.
    follow_up = client.get("/settings/email")
    fb = follow_up.json()
    assert fb["host"] == "smtp.qq.com"
    assert fb["user"] == "alerts@example.com"
    assert fb["password_configured"] is True
    assert fb["recipients"] == ["ops@example.com", "risk@example.com"]


def test_update_email_settings_placeholder_password_treated_as_unconfigured(
    client: TestClient,
) -> None:
    response = client.put(
        "/settings/email",
        json={"host": "smtp.qq.com", "user": "u@example.com", "password": "your-smtp-password"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["password_configured"] is False
    assert body["configured"] is False


def test_update_email_settings_omitting_password_keeps_existing_value(
    client: TestClient,
) -> None:
    client.put(
        "/settings/email",
        json={"host": "smtp.qq.com", "user": "u@example.com", "password": "first-secret"},
    )
    response = client.put("/settings/email", json={"host": "smtp.gmail.com"})

    assert response.status_code == 200
    body = response.json()
    assert body["host"] == "smtp.gmail.com"
    assert body["password_configured"] is True


def test_update_email_settings_clear_password_empties_secret(client: TestClient) -> None:
    client.put(
        "/settings/email",
        json={"host": "smtp.qq.com", "user": "u@example.com", "password": "first-secret"},
    )
    response = client.put("/settings/email", json={"clear_password": True})

    assert response.status_code == 200
    body = response.json()
    assert body["password_configured"] is False
    assert body["configured"] is False


def test_settings_email_response_never_exposes_password_field_value(
    client: TestClient,
) -> None:
    client.put(
        "/settings/email",
        json={"host": "smtp.qq.com", "user": "u@example.com", "password": "leak-me-please"},
    )
    response = client.get("/settings/email")

    body = response.json()
    assert "password" not in body, "GET response must not include a password field"
    assert "leak-me-please" not in response.text
