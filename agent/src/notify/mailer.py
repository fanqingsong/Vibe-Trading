"""SMTP send primitives.

The async entry point (:func:`send_email`) uses :mod:`aiosmtplib` to match the
FastAPI async style. A sync wrapper (:func:`send_email_sync`) bridges to
contexts without a running loop (CLI / tests). A :func:`send_test_email`
helper renders a fixed diagnostic body and is used by the
``POST /settings/email/test`` endpoint.

All sends append a record to ``~/.vibe-trading/notify/sent_log.jsonl``
(append-only ledger, mirroring ``live/audit.jsonl``). Failures are logged but
never raise out of the dispatcher path — callers that want exceptions should
use :func:`send_email` directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from src.notify.config import EmailConfig, load_email_config
from src.notify.paths import sent_log_path
from src.notify.renderer import render_template

logger = logging.getLogger(__name__)

# Default per-send SMTP timeout (seconds). Generous because some providers
# (corporate relays, international SMTP) are slow on the first connection.
_SMTP_TIMEOUT = 30.0


@dataclass
class EmailResult:
    """Outcome of one send attempt.

    Attributes:
        ok: True if the message was accepted by the SMTP server.
        message: Human-readable status (success note or error summary).
        latency_ms: Wall time spent in the SMTP transaction.
        recipients: The ``To`` list the send targeted.
        subject: The message subject.
        error: Structured error detail when ``ok`` is False, else ``None``.
    """

    ok: bool
    message: str
    latency_ms: int
    recipients: list[str]
    subject: str
    error: dict | None = None

    def to_dict(self) -> dict:
        """JSON-safe representation (used for the sent_log ledger)."""
        return asdict(self)


# ------------------------- public API -------------------------


async def send_email(
    *,
    to: str | Iterable[str],
    subject: str,
    html: str,
    config: EmailConfig | None = None,
    from_addr: str | None = None,
    timeout: float = _SMTP_TIMEOUT,
) -> EmailResult:
    """Send one HTML email asynchronously.

    Args:
        to: One recipient address or an iterable of them.
        subject: Message subject (plain text).
        html: Message body (HTML).
        config: SMTP config. Defaults to :func:`load_email_config`.
        from_addr: Override the envelope From. Defaults to ``config.effective_from``.
        timeout: Per-SMTP-command timeout in seconds.

    Returns:
        An :class:`EmailResult`. Never raises for SMTP-level failures — the
        error is captured in ``result.error``. Invalid config (missing host)
        is also returned as a failed result rather than raising.
    """
    cfg = config if config is not None else load_email_config()
    recipients = _normalize_recipients(to)
    sender = (from_addr or cfg.effective_from).strip()

    if not cfg.configured:
        result = _failed(
            recipients, subject, "SMTP is not configured (host/user/password missing)."
        )
        _log_send(result)
        return result
    if not recipients:
        result = _failed(recipients, subject, "No recipients specified.")
        _log_send(result)
        return result
    if not sender:
        result = _failed(recipients, subject, "No From address resolved.")
        _log_send(result)
        return result

    started = time.monotonic()
    try:
        await _send_via_aiosmtplib(cfg, sender, recipients, subject, html, timeout)
    except Exception as exc:  # noqa: BLE001 — surface any SMTP error as a result
        latency = int((time.monotonic() - started) * 1000)
        result = EmailResult(
            ok=False,
            message=f"SMTP send failed: {exc}",
            latency_ms=latency,
            recipients=recipients,
            subject=subject,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        _log_send(result)
        return result

    latency = int((time.monotonic() - started) * 1000)
    result = EmailResult(
        ok=True,
        message="Message accepted for delivery.",
        latency_ms=latency,
        recipients=recipients,
        subject=subject,
    )
    _log_send(result)
    return result


def send_email_sync(
    *,
    to: str | Iterable[str],
    subject: str,
    html: str,
    config: EmailConfig | None = None,
    from_addr: str | None = None,
    timeout: float = _SMTP_TIMEOUT,
) -> EmailResult:
    """Synchronous wrapper around :func:`send_email`.

    Uses :mod:`smtplib` directly (no event loop required) so CLI tools and
    tests can send without spinning up asyncio.
    """
    cfg = config if config is not None else load_email_config()
    recipients = _normalize_recipients(to)
    sender = (from_addr or cfg.effective_from).strip()

    if not cfg.configured:
        result = _failed(recipients, subject, "SMTP is not configured (host/user/password missing).")
        _log_send(result)
        return result
    if not recipients:
        result = _failed(recipients, subject, "No recipients specified.")
        _log_send(result)
        return result
    if not sender:
        result = _failed(recipients, subject, "No From address resolved.")
        _log_send(result)
        return result

    started = time.monotonic()
    try:
        _send_via_smtplib(cfg, sender, recipients, subject, html, timeout)
    except Exception as exc:  # noqa: BLE001
        latency = int((time.monotonic() - started) * 1000)
        result = EmailResult(
            ok=False,
            message=f"SMTP send failed: {exc}",
            latency_ms=latency,
            recipients=recipients,
            subject=subject,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        _log_send(result)
        return result

    latency = int((time.monotonic() - started) * 1000)
    result = EmailResult(
        ok=True,
        message="Message accepted for delivery.",
        latency_ms=latency,
        recipients=recipients,
        subject=subject,
    )
    _log_send(result)
    return result


async def send_test_email(
    config: EmailConfig | None = None,
    recipients: list[str] | None = None,
) -> EmailResult:
    """Send a fixed diagnostic email to verify SMTP wiring.

    Used by the ``POST /settings/email/test`` endpoint. The body names the
    resolved host / user so an operator can confirm which config is live.
    """
    cfg = config if config is not None else load_email_config()
    to = recipients or list(cfg.recipients)
    if not to and cfg.user:
        # Fall back to self-send so the test is usable without recipients set.
        to = [cfg.user]
    html = render_template(
        "system",
        title="Vibe-Trading test email",
        heading="Email notifications are wired up",
        body_lines=[
            f"SMTP host: {cfg.host or '(unset)'}",
            f"SMTP user: {cfg.user or '(unset)'}",
            "If you received this, outbound SMTP is working.",
        ],
        timestamp=_now_iso(),
    )
    return await send_email(
        to=to, subject="[Vibe-Trading] Test email", html=html, config=cfg
    )


# ------------------------- internals -------------------------


def _normalize_recipients(to: str | Iterable[str]) -> list[str]:
    """Flatten a recipient input into a clean list of addresses."""
    if isinstance(to, str):
        raw = [to]
    else:
        raw = list(to)
    return [addr.strip() for addr in raw if addr and addr.strip()]


def _failed(recipients: list[str], subject: str, message: str) -> EmailResult:
    return EmailResult(
        ok=False,
        message=message,
        latency_ms=0,
        recipients=recipients,
        subject=subject,
        error={"type": "ConfigError", "message": message},
    )


def _build_message(sender: str, recipients: list[str], subject: str, html: str) -> EmailMessage:
    """Build a MIME message with both text and HTML alternatives."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content("This message requires an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")
    return msg


async def _send_via_aiosmtplib(
    cfg: EmailConfig,
    sender: str,
    recipients: list[str],
    subject: str,
    html: str,
    timeout: float,
) -> None:
    """Send via aiosmtplib (async)."""
    import aiosmtplib  # local import: keeps import cost off non-send paths

    msg = _build_message(sender, recipients, subject, html)
    port = cfg.effective_port
    # Port 465 → implicit TLS (SMTPS); others → STARTTLS upgrade.
    use_ssl = port == 465
    if use_ssl:
        client = aiosmtplib.SMTP(
            hostname=cfg.host, port=port, use_tls=True, timeout=timeout
        )
    else:
        client = aiosmtplib.SMTP(
            hostname=cfg.host, port=port, use_tls=False, timeout=timeout
        )
    async with client:
        if not use_ssl:
            await client.starttls()
        if cfg.password:
            await client.login(cfg.user, cfg.password)
        await client.send_message(msg)


def _send_via_smtplib(
    cfg: EmailConfig,
    sender: str,
    recipients: list[str],
    subject: str,
    html: str,
    timeout: float,
) -> None:
    """Send via stdlib smtplib (sync)."""
    msg = _build_message(sender, recipients, subject, html)
    port = cfg.effective_port
    if port == 465:
        with smtplib.SMTP_SSL(cfg.host, port, timeout=timeout) as client:
            if cfg.password:
                client.login(cfg.user, cfg.password)
            client.send_message(msg)
    else:
        with smtplib.SMTP(cfg.host, port, timeout=timeout) as client:
            client.starttls()
            if cfg.password:
                client.login(cfg.user, cfg.password)
            client.send_message(msg)


def _now_iso() -> str:
    """UTC ISO8601 timestamp (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_send(result: EmailResult) -> None:
    """Append a send record to the JSONL ledger and emit a log line."""
    record = {
        "ts": _now_iso(),
        **result.to_dict(),
    }
    if result.ok:
        logger.info("email sent: %s -> %s (%dms)", result.subject, result.recipients, result.latency_ms)
    else:
        logger.warning(
            "email send failed: %s -> %s — %s",
            result.subject,
            result.recipients,
            result.message,
        )
    _append_ledger(record)


def _append_ledger(record: dict, path: Path | None = None) -> None:
    """Append one JSON record to the send ledger (best-effort)."""
    ledger = path if path is not None else sent_log_path()
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        # Ledger is observability-only; never fail a send over a ledger write.
        logger.debug("notify ledger write failed: %s", exc)
