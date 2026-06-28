"""Tests for the scheduled-task email-notification path.

Covers:
- ``dispatcher._map_event`` for ``scheduler.task.completed``: recipient
  resolution (per-task override → owner email → global), subject/kind for
  success vs failure, content truncation, and the ``notify_reports`` toggle.
- ``ScheduledTaskStore.get_task_by_session_id`` against the in-memory backend.
- ``SessionService._maybe_notify_scheduled_task`` gating on ``notify_enabled``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from src.notify import dispatcher
from src.notify.config import EmailConfig
from src.notify.dispatcher import (
    EVENT_SCHEDULER_TASK_COMPLETED,
    _map_event,
    _reset_throttle_state,
    dispatch_event,
)


def _cfg(**overrides) -> EmailConfig:
    base = dict(
        host="smtp.qq.com", port=465, user="u@x.com", password="p",
        from_addr="u@x.com", recipients=("global@x.com",),
    )
    base.update(overrides)
    return EmailConfig(**base)


def _reset() -> None:
    _reset_throttle_state()


# ----------------------- _map_event: basics -----------------------


def test_map_scheduler_task_completed_success() -> None:
    cfg = _cfg()
    mapped = _map_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {
            "task_id": "t1", "session_id": "s1", "attempt_id": "a1",
            "title": "Daily summary", "prompt": "Summarize NVDA",
            "summary": "NVDA rose 2% on earnings.", "error": "",
            "status": "completed", "owner_email": "owner@x.com",
        },
        cfg,
    )
    assert mapped is not None
    assert mapped.template == "scheduled_report"
    assert mapped.kind == "success"
    assert "completed" in mapped.subject
    assert mapped.recipients_override == ("owner@x.com",)
    assert mapped.extra_context["body"] == "NVDA rose 2% on earnings."
    assert mapped.extra_context["prompt"] == "Summarize NVDA"


def test_map_scheduler_task_failed_uses_error_and_error_tone() -> None:
    cfg = _cfg()
    mapped = _map_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {
            "title": "Watchdog", "status": "failed",
            "summary": "", "error": "boom", "owner_email": "o@x.com",
        },
        cfg,
    )
    assert mapped is not None
    assert mapped.kind == "error"
    assert "failed" in mapped.subject
    assert mapped.extra_context["body"] == "boom"


def test_map_scheduler_task_respects_notify_reports_toggle() -> None:
    cfg = _cfg(notify_reports=False)
    mapped = _map_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {"title": "t", "summary": "x", "owner_email": "o@x.com"},
        cfg,
    )
    assert mapped is None


# ----------------------- recipient resolution -----------------------


def test_recipients_per_task_override_wins_over_owner() -> None:
    cfg = _cfg()
    mapped = _map_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {
            "title": "t", "summary": "x", "owner_email": "owner@x.com",
            "recipients_override": ["a@x.com", "b@x.com"],
        },
        cfg,
    )
    assert mapped is not None
    assert mapped.recipients_override == ("a@x.com", "b@x.com")


def test_recipients_string_override_is_split() -> None:
    cfg = _cfg()
    mapped = _map_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {
            "title": "t", "summary": "x",
            "recipients_override": "a@x.com; b@x.com, c@x.com",
        },
        cfg,
    )
    assert mapped is not None
    assert mapped.recipients_override == ("a@x.com", "b@x.com", "c@x.com")


def test_recipients_fall_back_to_global_when_nothing_task_specific() -> None:
    """No task-specific recipient → override is empty; dispatch_event uses cfg.recipients."""
    cfg = _cfg(recipients=("ops@x.com", "ops2@x.com"))
    mapped = _map_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {"title": "t", "summary": "x", "owner_email": "", "recipients_override": []},
        cfg,
    )
    assert mapped is not None
    # _map_event leaves the override empty; dispatch_event fills from cfg.
    assert mapped.recipients_override == ()


@pytest.mark.asyncio
async def test_dispatch_scheduler_task_falls_back_to_global_recipients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset()
    cfg = _cfg(recipients=("ops@x.com",))
    captured: dict = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        from src.notify import mailer
        return mailer.EmailResult(
            ok=True, message="ok", latency_ms=1,
            recipients=list(kwargs["to"]), subject=kwargs["subject"],
        )

    monkeypatch.setattr(dispatcher, "send_email", fake_send)
    await dispatch_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {"title": "t", "summary": "x", "owner_email": ""},  # no task-specific recipient
        config=cfg,
    )
    assert captured["to"] == ["ops@x.com"]


# ----------------------- content truncation -----------------------


def test_long_summary_is_truncated() -> None:
    cfg = _cfg()
    long = "Z" * 6000
    mapped = _map_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {"title": "t", "summary": long, "owner_email": "o@x.com"},
        cfg,
    )
    assert mapped is not None
    body = mapped.extra_context["body"]
    assert len(body) < len(long)
    assert "truncated" in body.lower()


# ----------------------- dispatch_event end-to-end -----------------------


@pytest.mark.asyncio
async def test_dispatch_scheduler_task_sends_to_owner_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset()
    cfg = _cfg()
    captured: dict = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        from src.notify import mailer
        return mailer.EmailResult(
            ok=True, message="ok", latency_ms=1,
            recipients=list(kwargs["to"]), subject=kwargs["subject"],
        )

    monkeypatch.setattr(dispatcher, "send_email", fake_send)
    result = await dispatch_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {
            "task_id": "t1", "session_id": "s1", "title": "Daily",
            "prompt": "p", "summary": "Hello world", "status": "completed",
            "owner_email": "owner@x.com",
        },
        config=cfg,
    )
    assert result is not None and result.ok is True
    assert captured["to"] == ["owner@x.com"]
    assert "completed" in captured["subject"]
    assert "Hello world" in captured["html"]


@pytest.mark.asyncio
async def test_dispatch_scheduler_task_uses_override_recipients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset()
    cfg = _cfg()
    captured: dict = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        from src.notify import mailer
        return mailer.EmailResult(
            ok=True, message="ok", latency_ms=1,
            recipients=list(kwargs["to"]), subject=kwargs["subject"],
        )

    monkeypatch.setattr(dispatcher, "send_email", fake_send)
    await dispatch_event(
        EVENT_SCHEDULER_TASK_COMPLETED,
        {
            "title": "t", "summary": "x", "owner_email": "owner@x.com",
            "recipients_override": ["custom@x.com"],
        },
        config=cfg,
    )
    assert captured["to"] == ["custom@x.com"]


# ----------------------- ScheduledTaskStore.get_task_by_session_id -----------------------


def test_store_get_task_by_session_id_inmemory_hit() -> None:
    from src.scheduler.store import ScheduledTaskStore

    store = ScheduledTaskStore()
    task = SimpleNamespace(
        id="tid", user_id="uid", title="t", prompt="p",
        schedule_type="preset", schedule_preset="daily_0930", cron_expr=None,
        timezone="Asia/Shanghai", session_id="sess-1",
        enabled=True, on_overlap="skip",
        notify_enabled=True, notify_emails=None,
        last_run_at=None, last_status="idle", last_error=None,
        last_attempt_id=None, run_count=0,
        created_at=None, updated_at=None,
    )
    store._mem_backend()["tid"] = task
    found = store.get_task_by_session_id("sess-1")
    assert found is not None
    assert found.id == "tid"
    assert store.get_task_by_session_id("nope") is None
    assert store.get_task_by_session_id("") is None


# ----------------------- SessionService gating -----------------------


def test_maybe_notify_skips_when_notify_disabled() -> None:
    """A task without notify_enabled must not dispatch any email event."""
    from src.session.service import SessionService

    with mock.patch(
        "src.scheduler.store.ScheduledTaskStore.get_task_by_session_id"
    ) as m_get, mock.patch("src.notify.dispatcher.fire_and_forget") as m_faf:
        m_get.return_value = SimpleNamespace(
            id="t1", user_id="u1", session_id="s1", title="t", prompt="p",
            notify_enabled=False, notify_emails=None,
        )
        svc = SessionService.__new__(SessionService)
        svc._maybe_notify_scheduled_task(
            "s1", attempt_id="a1", status="completed", summary="hi", error="",
        )
    m_faf.assert_not_called()


def test_maybe_notify_dispatches_when_enabled() -> None:
    """A task with notify_enabled dispatches the scheduler.task.completed event."""
    from src.session.service import SessionService

    with mock.patch(
        "src.scheduler.store.ScheduledTaskStore.get_task_by_session_id"
    ) as m_get, mock.patch(
        "src.session.service.SessionService._lookup_owner_email",
        return_value="owner@x.com",
    ), mock.patch("src.notify.dispatcher.fire_and_forget") as m_faf:
        m_get.return_value = SimpleNamespace(
            id="t1", user_id="u1", session_id="s1", title="Daily", prompt="p",
            notify_enabled=True, notify_emails="a@x.com,b@x.com",
        )
        svc = SessionService.__new__(SessionService)
        svc._maybe_notify_scheduled_task(
            "s1", attempt_id="a1", status="completed", summary="hi", error="",
        )
    m_faf.assert_called_once()
    event_type, data = m_faf.call_args.args
    assert event_type == EVENT_SCHEDULER_TASK_COMPLETED
    assert data["title"] == "Daily"
    assert data["summary"] == "hi"
    assert data["owner_email"] == "owner@x.com"
    assert data["recipients_override"] == ["a@x.com", "b@x.com"]


def test_maybe_notify_skips_when_session_has_no_task() -> None:
    """An ordinary chat session (no matching task) must not trigger email."""
    from src.session.service import SessionService

    with mock.patch(
        "src.scheduler.store.ScheduledTaskStore.get_task_by_session_id",
        return_value=None,
    ) as m_get, mock.patch("src.notify.dispatcher.fire_and_forget") as m_faf:
        svc = SessionService.__new__(SessionService)
        svc._maybe_notify_scheduled_task(
            "ordinary-session", attempt_id="a1",
            status="completed", summary="hi", error="",
        )
    m_faf.assert_not_called()
