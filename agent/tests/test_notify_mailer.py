"""Unit tests for the notify subsystem (mailer / config / dispatcher / renderer).

These tests never hit a real SMTP server: the aiosmtplib / smtplib send paths
are patched at the module boundary. The dispatcher's in-memory throttle state is
reset between tests.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest

from src.notify import config as notify_config
from src.notify import dispatcher
from src.notify import mailer as notify_mailer
from src.notify.config import EmailConfig, load_email_config
from src.notify.dispatcher import dispatch_event, _reset_throttle_state
from src.notify.renderer import render_template


# ----------------------- config -----------------------


def test_email_config_unconfigured_when_host_missing() -> None:
    cfg = load_email_config({"SMTP_USER": "a@b.com", "SMTP_PASSWORD": "real"})
    assert not cfg.configured


def test_email_config_unconfigured_when_password_is_placeholder() -> None:
    cfg = load_email_config(
        {"SMTP_HOST": "smtp.qq.com", "SMTP_USER": "a@b.com", "SMTP_PASSWORD": "your-smtp-password"}
    )
    assert not cfg.configured


def test_email_config_configured_with_real_secret() -> None:
    cfg = load_email_config(
        {"SMTP_HOST": "smtp.qq.com", "SMTP_USER": "a@b.com", "SMTP_PASSWORD": "real"}
    )
    assert cfg.configured
    assert cfg.effective_from == "a@b.com"
    assert cfg.effective_port == 465  # default TLS port


def test_email_config_port_587_implies_starttls_port() -> None:
    cfg = load_email_config(
        {"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_USER": "a", "SMTP_PASSWORD": "p"}
    )
    assert cfg.effective_port == 587


def test_email_config_recipients_split_on_comma_and_semicolon() -> None:
    cfg = load_email_config({"NOTIFY_RECIPIENTS": "a@b.com; c@d.com, e@f.com"})
    assert cfg.recipients == ("a@b.com", "c@d.com", "e@f.com")


def test_email_config_masked_dict_hides_password() -> None:
    cfg = load_email_config(
        {"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASSWORD": "topsecret"}
    )
    masked = cfg.masked_dict()
    assert masked["password"] == notify_config.SMTP_SECRET_MASK
    assert masked["password_configured"] is True
    assert "topsecret" not in json.dumps(masked)


def test_email_config_masked_dict_empty_when_unconfigured() -> None:
    cfg = load_email_config({})
    assert cfg.masked_dict()["password"] == ""


# ----------------------- renderer -----------------------


def test_render_system_template_contains_heading_and_body() -> None:
    html = render_template(
        "system",
        title="T",
        heading="Hello world",
        body_lines=["line one", "line two"],
        kind="info",
        details={"audit_id": "la_123"},
        timestamp="2026-06-27",
        event_type="system",
    )
    assert "Hello world" in html
    assert "line one" in html
    assert "Vibe-Trading" in html


def test_render_trade_alert_template_includes_intent() -> None:
    html = render_template(
        "trade_alert",
        title="T",
        heading="Order filled",
        kind="success",
        body_lines=["Intent: buy 3 NVDA", "Broker: robinhood"],
        details={"audit_id": "la_abc"},
        timestamp="2026",
        event_type="live.action",
    )
    assert "Order filled" in html
    assert "buy 3 NVDA" in html


def test_render_report_template_supports_sections_and_chart() -> None:
    html = render_template(
        "report",
        title="T",
        heading="Daily report",
        kind="info",
        body_lines=["summary"],
        sections=[{"title": "PnL", "rows": [{"label": "Total", "value": "1.5%"}]}],
        chart_data_uri="data:image/png;base64,xyz",
        timestamp="2026",
        event_type="report",
    )
    assert "Daily report" in html
    assert "1.5%" in html
    assert "data:image/png;base64,xyz" in html


def test_render_unknown_template_raises() -> None:
    with pytest.raises(ValueError):
        render_template("does-not-exist")


# ----------------------- mailer (sync path, mocked) -----------------------


def _configured_cfg() -> EmailConfig:
    return EmailConfig(
        host="smtp.qq.com",
        port=465,
        user="alerts@example.com",
        password="real",
        from_addr="alerts@example.com",
        use_tls=True,
        recipients=("ops@example.com",),
    )


def test_send_email_sync_returns_failed_when_unconfigured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify_mailer, "sent_log_path", lambda: tmp_path / "sent_log.jsonl")
    cfg = load_email_config({})  # nothing configured
    result = notify_mailer.send_email_sync(
        to="x@y.com", subject="s", html="<b>x</b>", config=cfg
    )
    assert result.ok is False
    assert "not configured" in result.message.lower()


def test_send_email_sync_succeeds_and_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "sent_log.jsonl"
    monkeypatch.setattr(notify_mailer, "sent_log_path", lambda: log_path)
    cfg = _configured_cfg()

    with mock.patch.object(notify_mailer, "_send_via_smtplib") as mock_send:
        result = notify_mailer.send_email_sync(
            to="ops@example.com", subject="hello", html="<b>hi</b>", config=cfg
        )

    assert result.ok is True
    assert result.recipients == ["ops@example.com"]
    assert mock_send.call_count == 1
    # Ledger must record the successful send.
    assert log_path.exists()
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["ok"] is True
    assert record["subject"] == "hello"
    # The password must never enter the ledger.
    assert "real" not in log_path.read_text(encoding="utf-8")


def test_send_email_sync_captures_smtp_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify_mailer, "sent_log_path", lambda: tmp_path / "log.jsonl")
    cfg = _configured_cfg()

    with mock.patch.object(
        notify_mailer, "_send_via_smtplib", side_effect=OSError("connection refused")
    ):
        result = notify_mailer.send_email_sync(
            to="ops@example.com", subject="x", html="y", config=cfg
        )

    assert result.ok is False
    assert result.error is not None
    assert result.error["type"] == "OSError"
    assert "connection refused" in result.error["message"]


def test_send_email_sync_returns_failed_when_no_recipients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify_mailer, "sent_log_path", lambda: tmp_path / "log.jsonl")
    cfg = _configured_cfg()
    with mock.patch.object(notify_mailer, "_send_via_smtplib") as mock_send:
        result = notify_mailer.send_email_sync(to=[], subject="x", html="y", config=cfg)
    assert result.ok is False
    assert "recipients" in result.message.lower()
    mock_send.assert_not_called()


# ----------------------- mailer (async path, mocked) -----------------------


@pytest.mark.asyncio
async def test_send_email_async_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify_mailer, "sent_log_path", lambda: tmp_path / "log.jsonl")
    cfg = _configured_cfg()

    with mock.patch.object(notify_mailer, "_send_via_aiosmtplib", new=mock.AsyncMock()):
        result = await notify_mailer.send_email(
            to="ops@example.com", subject="async", html="x", config=cfg
        )

    assert result.ok is True


# ----------------------- dispatcher -----------------------


def _reset() -> None:
    _reset_throttle_state()


@pytest.mark.asyncio
async def test_dispatch_event_noop_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset()
    # Force unconfigured config.
    monkeypatch.setattr(dispatcher, "load_email_config", lambda: load_email_config({}))
    result = await dispatch_event("live.action", {"outcome": "filled"})
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_event_live_action_filled_sends_trade_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset()
    monkeypatch.setattr(notify_mailer, "sent_log_path", lambda: tmp_path / "log.jsonl")
    cfg = EmailConfig(
        host="smtp.qq.com", port=465, user="u@x.com", password="p",
        from_addr="u@x.com", recipients=("ops@x.com",),
    )
    sent_subjects: list[str] = []

    async def fake_send(**kwargs):
        sent_subjects.append(kwargs["subject"])
        return notify_mailer.EmailResult(
            ok=True, message="ok", latency_ms=1,
            recipients=list(kwargs["to"]), subject=kwargs["subject"],
        )

    monkeypatch.setattr(dispatcher, "send_email", fake_send)
    result = await dispatch_event(
        "live.action",
        {"outcome": "filled", "kind": "order_placed", "intent_normalized": "buy 3 NVDA",
         "server": "robinhood", "audit_id": "la_1", "session_id": "s1"},
        config=cfg,
    )
    assert result is not None and result.ok is True
    assert sent_subjects == ["[Vibe-Trading] Order filled: buy 3 NVDA"]


@pytest.mark.asyncio
async def test_dispatch_event_ignores_quiet_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset()
    cfg = EmailConfig(host="h", port=465, user="u", password="p", recipients=("o@x.com",))
    monkeypatch.setattr(dispatcher, "send_email", mock.AsyncMock())
    # "accepted" is not in the alert set -> no email.
    result = await dispatch_event("live.action", {"outcome": "accepted"}, config=cfg)
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_event_throttle_drops_burst(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset()
    cfg = EmailConfig(host="h", port=465, user="u", password="p", recipients=("o@x.com",))
    calls: list[str] = []

    async def fake_send(**kwargs):
        calls.append(kwargs["subject"])
        return notify_mailer.EmailResult(
            ok=True, message="ok", latency_ms=1,
            recipients=list(kwargs["to"]), subject=kwargs["subject"],
        )

    monkeypatch.setattr(dispatcher, "send_email", fake_send)
    data = {"outcome": "filled", "intent_normalized": "buy NVDA", "audit_id": "la"}
    await dispatch_event("live.action", data, config=cfg)
    # Second send within the 5s window -> throttled to None.
    result2 = await dispatch_event("live.action", data, config=cfg)
    assert result2 is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_dispatch_event_mandate_committed_uses_system_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset()
    cfg = EmailConfig(host="h", port=465, user="u", password="p", recipients=("o@x.com",))
    captured: dict = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return notify_mailer.EmailResult(
            ok=True, message="ok", latency_ms=1, recipients=list(kwargs["to"]), subject=kwargs["subject"],
        )

    monkeypatch.setattr(dispatcher, "send_email", fake_send)
    await dispatch_event(
        "mandate.committed",
        {"mandate_snapshot_ref": "m_abc", "session_id": "s1"},
        config=cfg,
    )
    assert "Mandate committed" in captured["subject"]


@pytest.mark.asyncio
async def test_dispatch_event_trade_alerts_toggle_off_blocks_live_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset()
    cfg = EmailConfig(
        host="h", port=465, user="u", password="p",
        recipients=("o@x.com",), notify_trade_alerts=False,
    )
    monkeypatch.setattr(dispatcher, "send_email", mock.AsyncMock())
    result = await dispatch_event("live.action", {"outcome": "filled"}, config=cfg)
    assert result is None


def test_fire_and_forget_without_running_loop_is_noop() -> None:
    _reset()
    # No running loop in a sync test context.
    task = dispatcher.fire_and_forget("live.action", {"outcome": "filled"})
    assert task is None
