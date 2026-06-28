"""Unit tests for the SendEmailTool — the agent-facing email tool.

These tests never hit a real SMTP server: the sync send path is patched at the
module boundary. Config is injected via monkeypatched env vars.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from src.notify import mailer as notify_mailer
from src.notify.config import EmailConfig, load_email_config
from src.tools.send_email_tool import SendEmailTool


# Minimal env that makes ``EmailConfig.configured`` return True.
_CONFIGURED_ENV = {
    "SMTP_HOST": "smtp.qq.com",
    "SMTP_PORT": "465",
    "SMTP_USER": "alerts@example.com",
    "SMTP_PASSWORD": "real-secret",
    "SMTP_FROM": "alerts@example.com",
    "SMTP_USE_TLS": "true",
    "NOTIFY_RECIPIENTS": "default1@example.com, default2@example.com",
}


def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all SMTP_* / NOTIFY_* vars so tests start from a known state."""
    for key in [
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
        "SMTP_USE_TLS", "NOTIFY_RECIPIENTS", "NOTIFY_TRADE_ALERTS", "NOTIFY_REPORTS",
    ]:
        monkeypatch.delenv(key, raising=False)


# ----------------------- validation errors -----------------------


def test_missing_subject_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_env(monkeypatch)
    result = json.loads(SendEmailTool().execute(body="hello", to="a@b.com"))
    assert result["status"] == "error"
    assert "subject" in result["error"]


def test_missing_body_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_env(monkeypatch)
    result = json.loads(SendEmailTool().execute(subject="hi", to="a@b.com"))
    assert result["status"] == "error"
    assert "body" in result["error"]


# ----------------------- unconfigured SMTP -----------------------


def test_unconfigured_smtp_returns_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_env(monkeypatch)
    result = json.loads(SendEmailTool().execute(
        subject="hi", body="hello", to="a@b.com"
    ))
    assert result["status"] == "error"
    assert "not configured" in result["error"].lower()
    # Must tell the user how to fix it.
    assert "SMTP_HOST" in result["error"]


def test_no_recipient_and_empty_notify_recipients_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_env(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "smtp.qq.com")
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "real-secret")
    result = json.loads(SendEmailTool().execute(subject="hi", body="hello"))
    assert result["status"] == "error"
    assert "recipient" in result["error"].lower()


# ----------------------- happy path -----------------------


@pytest.fixture
def configured_smtp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Configure SMTP and redirect the send ledger to a temp file."""
    _reset_env(monkeypatch)
    for k, v in _CONFIGURED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(notify_mailer, "sent_log_path", lambda: tmp_path / "log.jsonl")


def test_send_to_explicit_recipient_succeeds(configured_smtp) -> None:
    captured: dict = {}

    def fake_send(cfg, sender, recipients, subject, html, timeout):
        captured.update(sender=sender, recipients=recipients, subject=subject, html=html)

    with mock.patch.object(notify_mailer, "_send_via_smtplib", side_effect=fake_send):
        result = json.loads(SendEmailTool().execute(
            subject="Analysis report",
            body="Stock A: BUY\n  PE: 15",
            to="user@example.com",
        ))

    assert result["status"] == "ok"
    assert result["ok"] is True
    assert result["recipients"] == ["user@example.com"]
    assert result["subject"] == "Analysis report"
    assert captured["recipients"] == ["user@example.com"]
    assert captured["sender"] == "alerts@example.com"
    # Plain-text body is wrapped with a pre-wrap container preserving newlines.
    assert "Stock A: BUY" in captured["html"]
    assert "\n" in captured["html"]


def test_send_falls_back_to_default_recipients(configured_smtp) -> None:
    captured: dict = {}

    def fake_send(cfg, sender, recipients, subject, html, timeout):
        captured["recipients"] = list(recipients)

    with mock.patch.object(notify_mailer, "_send_via_smtplib", side_effect=fake_send):
        result = json.loads(SendEmailTool().execute(
            subject="Daily summary", body="All good."
        ))

    assert result["status"] == "ok"
    assert result["recipients"] == ["default1@example.com", "default2@example.com"]
    assert captured["recipients"] == ["default1@example.com", "default2@example.com"]


def test_semicolon_separated_recipients_are_split(configured_smtp) -> None:
    captured: dict = {}

    def fake_send(cfg, sender, recipients, subject, html, timeout):
        captured["recipients"] = list(recipients)

    with mock.patch.object(notify_mailer, "_send_via_smtplib", side_effect=fake_send):
        result = json.loads(SendEmailTool().execute(
            subject="s", body="b", to="a@x.com; b@x.com, c@x.com"
        ))

    assert result["status"] == "ok"
    assert result["recipients"] == ["a@x.com", "b@x.com", "c@x.com"]


def test_plain_text_body_escapes_html(configured_smtp) -> None:
    captured: dict = {}

    def fake_send(cfg, sender, recipients, subject, html, timeout):
        captured["html"] = html

    with mock.patch.object(notify_mailer, "_send_via_smtplib", side_effect=fake_send):
        SendEmailTool().execute(
            subject="x", body="<script>alert(1)</script>", to="y@z.com"
        )

    # Default body_format='text' must escape, never pass raw <script> through.
    assert "<script>" not in captured["html"]
    assert "&lt;script&gt;" in captured["html"]


def test_html_body_format_passes_through_verbatim(configured_smtp) -> None:
    captured: dict = {}

    def fake_send(cfg, sender, recipients, subject, html, timeout):
        captured["html"] = html

    with mock.patch.object(notify_mailer, "_send_via_smtplib", side_effect=fake_send):
        SendEmailTool().execute(
            subject="x",
            body="<b>Bold</b> <i>report</i>",
            body_format="html",
            to="h@x.com",
        )

    assert captured["html"] == "<b>Bold</b> <i>report</i>"


def test_smtp_failure_is_surfaced_not_raised(configured_smtp) -> None:
    with mock.patch.object(
        notify_mailer, "_send_via_smtplib", side_effect=OSError("connection refused")
    ):
        result = json.loads(SendEmailTool().execute(
            subject="x", body="y", to="a@b.com"
        ))

    assert result["status"] == "error"
    assert result["ok"] is False
    assert result["error"]["type"] == "OSError"
    assert "connection refused" in result["error"]["message"]


# ----------------------- registry integration -----------------------


def test_tool_is_auto_registered() -> None:
    """The tool must be discovered by the auto-discovery registry."""
    from src.tools import build_registry

    registry = build_registry()
    assert "send_email" in registry.tool_names
    tool = registry.get("send_email")
    assert tool is not None
    assert tool.is_readonly is False
    assert tool.repeatable is True
