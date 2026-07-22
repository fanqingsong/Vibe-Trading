"""POST /buy-points/email and /chanlun/email — screen result email endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server
from src.notify.mailer import EmailResult


BUY_POINT_PAYLOAD = {
    "universe": "csi300",
    "market": "a_share",
    "trade_date": "20260722",
    "require_volume": True,
    "volume_mult": 1.2,
    "universe_size": 300,
    "fetched": 300,
    "matched": 5,
    "count": 1,
    "source": "tushare",
    "results": [
        {
            "code": "600036.SH",
            "name": "招商银行",
            "signal_date": "2026-07-20",
            "breakout_date": "2026-07-10",
            "prior_high": 40.5,
            "pullback_low": 39.8,
            "close": 41.2,
            "breakout_pct": 2.1,
            "volume_ratio": 1.5,
            "days_since_signal": 2,
        }
    ],
}

CHANLUN_PAYLOAD = {
    "universe": "csi300",
    "market": "a_share",
    "trade_date": "2026-07-22",
    "buy_type": "buy3",
    "buy_label": "三买",
    "signal_freshness": 10,
    "ma_period": 34,
    "universe_size": 300,
    "fetched": 300,
    "matched": 8,
    "count": 1,
    "source": "sina",
    "results": [
        {
            "code": "600036.SH",
            "name": "招商银行",
            "signal_date": "2026-07-18",
            "buy_type": "buy3",
            "buy_label": "三买",
            "signal_detail": "三买_中枢上沿",
            "close": 41.2,
            "zg": 40.0,
            "zd": 38.5,
            "days_since_signal": 4,
        }
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


def test_buy_points_email_rejects_empty_results(client: TestClient, override_auth) -> None:
    payload = {**BUY_POINT_PAYLOAD, "results": [], "count": 0}
    response = client.post("/buy-points/email", json=payload)
    assert response.status_code == 400


def test_buy_points_email_sends_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, override_auth
) -> None:
    captured: dict = {}

    async def _fake_send_email(*, to, subject, html, config=None, **_kwargs):
        captured["subject"] = subject
        captured["html"] = html
        recipients = [to] if isinstance(to, str) else list(to)
        return EmailResult(
            ok=True, message="sent", latency_ms=5, recipients=recipients, subject=subject
        )

    monkeypatch.setattr("src.notify.mailer.send_email", _fake_send_email)
    response = client.post("/buy-points/email", json=BUY_POINT_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["recipients"] == ["user@example.com"]
    assert "600036.SH" in captured["html"]
    assert "Buy Points" in captured["subject"]


def test_chanlun_email_sends_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, override_auth
) -> None:
    captured: dict = {}

    async def _fake_send_email(*, to, subject, html, config=None, **_kwargs):
        captured["subject"] = subject
        captured["html"] = html
        recipients = [to] if isinstance(to, str) else list(to)
        return EmailResult(
            ok=True, message="sent", latency_ms=5, recipients=recipients, subject=subject
        )

    monkeypatch.setattr("src.notify.mailer.send_email", _fake_send_email)
    response = client.post("/chanlun/email", json=CHANLUN_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "600036.SH" in captured["html"]
    assert "三买" in captured["html"]
    assert "Chanlun" in captured["subject"]
