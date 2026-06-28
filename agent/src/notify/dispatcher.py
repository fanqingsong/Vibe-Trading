"""Event → email dispatch with throttle / dedupe.

The dispatcher is the bridge between the runtime event bus and the SMTP
mailer. It maps a small set of high-signal runtime events to email templates,
applies a per-(event-class) throttle window so a burst of fills does not flood
an inbox, then forwards to :func:`src.notify.mailer.send_email`.

Throttle is in-memory and process-local (single-process FastAPI). State lives
only for the lifetime of the process; a restart resets all windows. This is
intentional — persistence here would be over-engineering for a notification
pathway whose worst case is "a duplicate email after restart".

Concurrency note: :func:`dispatch_event` is async and safe to fire-and-forget
from a sync ``event_callback`` via ``asyncio.create_task``. All failures are
caught and logged; the dispatcher never raises into the event bus.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.notify.config import EmailConfig, load_email_config
from src.notify.mailer import EmailResult, send_email
from src.notify.renderer import render_template

logger = logging.getLogger(__name__)

# Event types the dispatcher knows how to turn into emails. These match the
# canonical event names emitted by the live / mandate / enforcement layers
# (see src/live/audit.py::_LIVE_ACTION_EVENT and src/live/mandate/commit.py).
EVENT_LIVE_ACTION = "live.action"
EVENT_MANDATE_COMMITTED = "mandate.committed"
EVENT_LIVE_HALTED = "live.halted"
EVENT_SCHEDULER_TASK_COMPLETED = "scheduler.task.completed"

# Outcomes on ``live.action`` that warrant an alert. ``accepted`` (the order
# was forwarded to the broker but not yet filled) and ``blocked`` (the gate
# refused it) are deliberately quieter — accepted is too chatty and blocked is
# surfaced through the mandate-proposal UI.
_ALERT_OUTCOMES: frozenset[str] = frozenset({"filled", "rejected", "error"})

# Throttle window per event class, in seconds. A second event of the same class
# within the window is dropped (not queued) — we prefer "missed one during a
# burst" over "inbox flooded". A window of 0 means no throttling.
_THROTTLE_WINDOWS: Mapping[str, float] = {
    EVENT_LIVE_ACTION: 5.0,
    EVENT_MANDATE_COMMITTED: 30.0,
    EVENT_LIVE_HALTED: 60.0,
    EVENT_SCHEDULER_TASK_COMPLETED: 0.0,
}


@dataclass
class _ThrottleState:
    """Per-event-class last-sent timestamp."""

    last_sent: dict[str, float] = field(default_factory=dict)

    def should_send(self, key: str, window: float) -> bool:
        """True if enough time has elapsed since the last send of ``key``."""
        now = time.monotonic()
        last = self.last_sent.get(key)
        if last is not None and (now - last) < window:
            return False
        self.last_sent[key] = now
        return True


# Process-global throttle state. Single FastAPI process → one state is enough.
# Tests reset this via :func:`_reset_throttle_state`.
_throttle = _ThrottleState()


def _reset_throttle_state() -> None:
    """Clear the throttle memory (test helper)."""
    _throttle.last_sent.clear()


async def dispatch_event(
    event_type: str,
    data: Mapping[str, Any],
    *,
    config: EmailConfig | None = None,
) -> EmailResult | None:
    """Map a runtime event to an email and send it.

    Returns ``None`` when the event is not email-eligible (not in the known
    set, the outcome is quiet, the relevant toggle is off, or throttled).
    Returns an :class:`EmailResult` for sends (including failed sends), so the
    caller can inspect delivery status.

    Never raises — failures are logged and returned.

    Args:
        event_type: The canonical event name (e.g. ``"live.action"``).
        data: The event payload, already redacted upstream by
            ``write_live_action`` / ``redact_payload``.
        config: Optional explicit config; defaults to :func:`load_email_config`.
    """
    cfg = config if config is not None else load_email_config()
    if not cfg.configured:
        return None

    mapped = _map_event(event_type, data, cfg)
    if mapped is None:
        return None

    # Throttle by (event_type) — the class-level signal is what floods inboxes.
    window = _THROTTLE_WINDOWS.get(event_type, 0.0)
    if window and not _throttle.should_send(event_type, window):
        logger.debug("dispatch_event throttled %s", event_type)
        return None

    # Per-event recipient override wins over the global config list.
    recipients = list(mapped.recipients_override) if mapped.recipients_override else list(cfg.recipients)
    if not recipients:
        logger.debug("dispatch_event %s: no recipients configured", event_type)
        return None

    render_context: dict[str, Any] = dict(
        title=mapped.title,
        heading=mapped.heading,
        body_lines=mapped.body_lines,
        kind=mapped.kind,
        details=mapped.details,
        event_type=event_type,
        timestamp=data.get("ts") if isinstance(data, Mapping) else None,
    )
    render_context.update(mapped.extra_context)
    html = render_template(mapped.template, **render_context)
    return await send_email(
        to=recipients, subject=mapped.subject, html=html, config=cfg
    )


@dataclass
class _MappedEvent:
    """A runtime event resolved into email template inputs."""

    template: str
    title: str
    heading: str
    subject: str
    kind: str
    body_lines: list[str]
    details: dict[str, Any]
    # Extra template variables beyond the standard set (title/heading/body_lines/
    # kind/details/event_type/timestamp). Used by templates that need bespoke
    # context, e.g. scheduled_report's prompt/body/web_url.
    extra_context: dict[str, Any] = field(default_factory=dict)
    # Per-event recipient override. When non-empty, replaces cfg.recipients.
    # Used by scheduled-task events whose owner email differs from the global
    # NOTIFY_RECIPIENTS list.
    recipients_override: tuple[str, ...] = ()


def _map_event(
    event_type: str,
    data: Mapping[str, Any],
    cfg: EmailConfig,
) -> _MappedEvent | None:
    """Translate one event into template inputs, or None if it should not email.

    Returns None when:
        * the event type is not a known notification event,
        * a live.action has a non-alert outcome (accepted/blocked),
        * the relevant feature toggle (notify_trade_alerts) is off.
    """
    if event_type == EVENT_LIVE_ACTION:
        if not cfg.notify_trade_alerts:
            return None
        outcome = str(data.get("outcome") or "").strip().lower()
        if outcome not in _ALERT_OUTCOMES:
            return None
        kind = str(data.get("kind") or outcome)
        intent = str(data.get("intent_normalized") or "(no intent)")
        broker = str(data.get("server") or "unknown")
        error = data.get("error")
        body = [f"Intent: {intent}", f"Broker: {broker}", f"Outcome: {outcome}"]
        if error:
            body.append(f"Error: {error}")
        tone = "error" if outcome in {"rejected", "error"} else "success"
        subject = f"[Vibe-Trading] Order {outcome}: {intent}"
        return _MappedEvent(
            template="trade_alert",
            title="Trade alert",
            heading=f"Order {outcome}",
            subject=subject,
            kind=tone,
            body_lines=body,
            details={"audit_id": data.get("audit_id"), "session_id": data.get("session_id")},
        )

    if event_type == EVENT_MANDATE_COMMITTED:
        if not cfg.notify_trade_alerts:
            return None
        ref = data.get("mandate_snapshot_ref") or data.get("mandate_id") or "(unknown)"
        body = ["A new live-trading mandate has been committed and is now active."]
        return _MappedEvent(
            template="system",
            title="Mandate committed",
            heading="Live-trading mandate is now active",
            subject="[Vibe-Trading] Mandate committed",
            kind="info",
            body_lines=body,
            details={"mandate_ref": ref, "session_id": data.get("session_id")},
        )

    if event_type == EVENT_LIVE_HALTED:
        if not cfg.notify_trade_alerts:
            return None
        reason = data.get("reason") or data.get("error") or "Kill-switch tripped"
        body = ["The live-trading channel has been halted.", f"Reason: {reason}"]
        return _MappedEvent(
            template="system",
            title="Live channel halted",
            heading="Live-trading channel halted",
            subject="[Vibe-Trading] Live channel HALTED",
            kind="error",
            body_lines=body,
            details={"session_id": data.get("session_id")},
        )

    if event_type == EVENT_SCHEDULER_TASK_COMPLETED:
        return _map_scheduler_task(data, cfg)

    return None


def _map_scheduler_task(
    data: Mapping[str, Any], cfg: EmailConfig
) -> _MappedEvent | None:
    """Map a scheduled-task completion event to an email.

    ``data`` keys:
        task_id, session_id, title, prompt, summary, error, attempt_id,
        status (``completed`` | ``failed``), owner_email (optional),
        recipients_override (optional list[str] — per-task notify_emails).

    Respects the ``notify_reports`` toggle. Recipients resolve as:
    per-task override → owner email → global config list.
    """
    if not cfg.notify_reports:
        return None

    title = str(data.get("title") or "Scheduled task")
    status = str(data.get("status") or "completed").lower()
    is_error = status == "failed" or bool(data.get("error"))
    summary = str(data.get("summary") or "")
    error = str(data.get("error") or "")
    body = error if is_error else (summary or "(no content)")

    # Truncate very long agent replies so the email stays readable; the full
    # transcript is always available via the Web UI link.
    max_chars = 4000
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n…(truncated; see full response in the Web UI)"

    owner_email = str(data.get("owner_email") or "").strip()
    raw_override = data.get("recipients_override")
    override: tuple[str, ...] = ()
    if isinstance(raw_override, (list, tuple)):
        override = tuple(str(x).strip() for x in raw_override if str(x).strip())
    elif isinstance(raw_override, str) and raw_override.strip():
        override = tuple(
            s.strip() for s in raw_override.replace(";", ",").split(",") if s.strip()
        )

    # Task-specific recipients: per-task override, else owner email. When
    # neither resolves we leave the override empty so ``dispatch_event`` falls
    # back to the global ``cfg.recipients`` list.
    recipients = override or ((owner_email,) if owner_email else ())

    subject_suffix = "failed" if is_error else "completed"
    kind = "error" if is_error else "success"
    return _MappedEvent(
        template="scheduled_report",
        title=title,
        heading=f"{title} — {subject_suffix}",
        subject=f"[Vibe-Trading] {title} {subject_suffix}",
        kind=kind,
        body_lines=[],
        details={
            "task_id": data.get("task_id"),
            "session_id": data.get("session_id"),
            "attempt_id": data.get("attempt_id"),
            "status": status,
        },
        extra_context={
            "prompt": str(data.get("prompt") or ""),
            "body": body,
            "web_url": str(data.get("web_url") or ""),
        },
        recipients_override=tuple(recipients) if recipients else (),
    )


def fire_and_forget(event_type: str, data: Mapping[str, Any]) -> asyncio.Task | None:
    """Schedule dispatch_event on the running loop without awaiting.

    Designed for use from a sync ``event_callback`` (e.g.
    ``SessionService._run_attempt``) that cannot await. If no loop is running,
    this is a no-op and logs a debug line — the event is still recorded in the
    audit ledger / SSE bus by the caller; only the email side-channel is lost.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("fire_and_forget(%s): no running loop; email skipped", event_type)
        return None
    return loop.create_task(_safe_dispatch(event_type, data))


async def _safe_dispatch(event_type: str, data: Mapping[str, Any]) -> None:
    """Wrap dispatch_event so it can never raise into the event bus."""
    try:
        await dispatch_event(event_type, data)
    except Exception:
        logger.warning("email dispatch for %s failed", event_type, exc_info=True)
