"""POST /dividends/email — send the on-screen high-dividend results by email."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server
from src.notify.mailer import EmailResult


SAMPLE_PAYLOAD = {
    "universe": "csi300",
    "market": "a_share",
    "trade_date": "20260722",
    "min_yield": 3.0,
    "max_yield": None,
    "min_market_cap": None,
    "max_pe": None,
    "market_cap_unit": "CNY_yi",
    "universe_size": 300,
    "matched": 12,
    "count": 2,
    "source": "tushare",
    "results": [
        {
            "code": "601088.SH",
            "name": "中国神华",
            "dividend_yield": 5.21,
            "pe": 12.3,
            "pb": 1.4,
            "market_cap": 8200.5,
            "close": 38.5,
        },
        {
            "code": "600900.SH",
            "name": "长江电力",
            "dividend_yield": 4.10,
            "pe": 18.0,
            "pb": 2.1,
            "market_cap": 6500.0,
            "close": 28.2,
        },
    ],
}


class _FakeUser:
    def __init__(self, email: str) -> None:
        self.id = "u_test"
        self.email = email
        self.name = "Test"
        self.is_active = True


async def _fake_require_user():
    return _FakeUser("user@example.com")


@pytest.fixture
def client(db_session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_baostock_supported", lambda: False)
    monkeypatch.setattr(api_server, "_baostock_installed", lambda: False)
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


@pytest.fixture
def override_auth():
    api_server.app.dependency_overrides[api_server.require_local_or_auth] = _fake_require_user
    yield
    api_server.app.dependency_overrides.pop(api_server.require_local_or_auth, None)


def test_dividends_email_rejects_empty_results(client: TestClient, override_auth) -> None:
    payload = {**SAMPLE_PAYLOAD, "results": [], "count": 0, "matched": 0}
    response = client.post("/dividends/email", json=payload)
    assert response.status_code == 400
    assert "results" in response.json()["detail"].lower()


def test_dividends_email_sends_to_user_and_returns_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, override_auth
) -> None:
    captured: dict = {}

    async def _fake_send_email(*, to, subject, html, config=None, **_kwargs):
        captured["to"] = to
        captured["subject"] = subject
        captured["html"] = html
        recipients = [to] if isinstance(to, str) else list(to)
        return EmailResult(
            ok=True,
            message="sent",
            latency_ms=12,
            recipients=recipients,
            subject=subject,
        )

    monkeypatch.setattr("src.notify.mailer.send_email", _fake_send_email)

    response = client.post("/dividends/email", json=SAMPLE_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["recipients"] == ["user@example.com"]
    assert "601088.SH" in captured["html"]
    assert "中国神华" in captured["html"]
    assert "High Dividend" in captured["subject"]


def test_dividends_email_reports_smtp_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, override_auth
) -> None:
    async def _fail_send(*, to, subject, html, config=None, **_kwargs):
        return EmailResult(
            ok=False,
            message="SMTP is not configured (host/user/password missing).",
            latency_ms=0,
            recipients=["user@example.com"],
            subject=subject,
            error={"type": "ConfigError", "message": "SMTP is not configured"},
        )

    monkeypatch.setattr("src.notify.mailer.send_email", _fail_send)

    response = client.post("/dividends/email", json=SAMPLE_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "SMTP" in body["message"]
