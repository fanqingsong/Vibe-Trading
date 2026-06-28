"""Scheduled-task HTTP routes for the Web UI.

Mounted by ``agent/api_server.py`` via
``register_scheduler_routes(app, require_auth=...)``. Follows the same
mounting pattern as :mod:`src.api.alpha_routes`.

Routes (all auth-gated, all scoped to ``current_user.id``):

- ``GET    /scheduler/tasks``                — list current user's tasks
- ``POST   /scheduler/tasks``                — create task (+ dedicated session)
- ``GET    /scheduler/tasks/{id}``           — task detail (incl. live next_run_at)
- ``PATCH  /scheduler/tasks/{id}``           — update fields
- ``DELETE /scheduler/tasks/{id}``           — delete (unregisters from scheduler)
- ``POST   /scheduler/tasks/{id}/run``       — manual trigger
- ``POST   /scheduler/tasks/{id}/toggle``    — enable / disable
- ``GET    /scheduler/presets``              — preset catalog for the UI picker
- ``GET    /scheduler/status``               — scheduler health snapshot

Per-user isolation: every read/write is filtered by ``current_user.id``; a
non-owner hitting ``/tasks/{id}`` gets a 404 (existence is hidden).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from src.db.models import ScheduledTask
from src.scheduler.cron import (
    PRESET_LABELS,
    VALID_PRESETS,
    humanize_schedule,
    next_run_ms_from_now,
)
from src.scheduler.models import TaskCreate, TaskOut, TaskUpdate
from src.scheduler.service import SchedulerService
from src.scheduler.store import ScheduledTaskStore

logger = logging.getLogger(__name__)

#: Process-wide service singleton. Constructed in
#: :func:`register_scheduler_routes` and started from the api_server lifespan.
_service: SchedulerService | None = None
_store: ScheduledTaskStore | None = None


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_uuid() -> str:
    import uuid

    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #


def _task_to_out(task: ScheduledTask) -> TaskOut:
    """Serialize a task row to the wire model, including live next_run_at."""
    next_run_at_iso: str | None = None
    schedule_label: str | None = None
    try:
        next_ms = next_run_ms_from_now(
            schedule_type=task.schedule_type,
            preset=task.schedule_preset,
            cron_expr=task.cron_expr,
            timezone_name=task.timezone or "Asia/Shanghai",
        )
        next_run_at_iso = datetime.fromtimestamp(next_ms / 1000, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001 — a bad schedule should not break listing
        next_run_at_iso = None
    try:
        schedule_label = humanize_schedule(
            schedule_type=task.schedule_type,
            preset=task.schedule_preset,
            cron_expr=task.cron_expr,
            timezone_name=task.timezone or "Asia/Shanghai",
        )
    except Exception:  # noqa: BLE001
        schedule_label = None

    return TaskOut(
        id=task.id,
        user_id=task.user_id,
        title=task.title,
        prompt=task.prompt,
        schedule_type=task.schedule_type,
        schedule_preset=task.schedule_preset,
        cron_expr=task.cron_expr,
        timezone=task.timezone or "Asia/Shanghai",
        session_id=task.session_id,
        enabled=bool(task.enabled),
        on_overlap=task.on_overlap or "skip",
        notify_enabled=bool(getattr(task, "notify_enabled", False)),
        notify_emails=getattr(task, "notify_emails", None),
        last_run_at=task.last_run_at.isoformat() if task.last_run_at else None,
        last_status=task.last_status or "idle",
        last_error=task.last_error,
        last_attempt_id=task.last_attempt_id,
        run_count=task.run_count or 0,
        created_at=task.created_at.isoformat() if task.created_at else "",
        updated_at=task.updated_at.isoformat() if task.updated_at else "",
        next_run_at=next_run_at_iso,
        schedule_label=schedule_label,
    )


# --------------------------------------------------------------------------- #
# Session factory binding
# --------------------------------------------------------------------------- #


def _default_session_factory(user_id: str) -> Any:
    """Resolve the per-user SessionService from the host api_server module.

    Importing :mod:`api_server` directly here would create a circular import
    (api_server imports this module at registration time), so we resolve it
    lazily through ``sys.modules`` on first call. Bound by the host in
    :func:`register_scheduler_routes` when available.
    """
    import sys

    host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
    if host is None:
        return None
    factory = getattr(host, "_get_session_service", None)
    if factory is None:
        return None
    return factory(user_id)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_scheduler_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
    session_factory: Callable[[str], Any] | None = None,
) -> None:
    """Mount the scheduler routes onto ``app`` and start the service.

    Args:
        app: The host FastAPI app.
        require_auth: Header-auth dependency for JSON endpoints. Resolved from
            ``api_server`` via ``sys.modules`` when ``None`` (mirrors the
            alpha_routes pattern).
        session_factory: Optional ``(user_id) -> SessionService``. Defaults to
            :func:`_default_session_factory` which resolves
            ``api_server._get_session_service`` lazily.
    """
    global _service, _store

    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:  # pragma: no cover — only on weird import setups
            raise RuntimeError(
                "register_scheduler_routes: api_server module not in sys.modules; "
                "pass require_auth explicitly"
            )
        require_auth = host.require_auth

    _store = ScheduledTaskStore()
    _service = SchedulerService(
        session_factory=session_factory or _default_session_factory,
        store=_store,
    )

    # ------------------------------------------------------------------- #
    # GET /scheduler/presets
    # ------------------------------------------------------------------- #

    @app.get("/scheduler/presets", dependencies=[Depends(require_auth)])
    async def list_presets() -> dict[str, Any]:
        """Return the preset catalog for the UI schedule picker."""
        return {
            "status": "ok",
            "presets": [
                {"key": k, "label": PRESET_LABELS[k]}
                for k in PRESET_LABELS
                if k in VALID_PRESETS
            ],
        }

    # ------------------------------------------------------------------- #
    # GET /scheduler/status
    # ------------------------------------------------------------------- #

    @app.get("/scheduler/status", dependencies=[Depends(require_auth)])
    async def scheduler_status() -> dict[str, Any]:
        """Return a health snapshot of the scheduler service."""
        if _service is None:  # pragma: no cover — always set by register_*
            return {"status": "ok", "running": False, "job_count": 0}
        snap = _service.status()
        return {"status": "ok", **snap}

    # ------------------------------------------------------------------- #
    # GET /scheduler/tasks
    # ------------------------------------------------------------------- #

    @app.get("/scheduler/tasks", dependencies=[Depends(require_auth)])
    async def list_tasks(current_user=Depends(require_auth)) -> dict[str, Any]:
        """List all tasks owned by the current user."""
        assert _store is not None  # noqa: S101 — guaranteed by register_*
        rows = _store.list_tasks(current_user.id)
        return {
            "status": "ok",
            "tasks": [_task_to_out(t).model_dump() for t in rows],
            "total": len(rows),
        }

    # ------------------------------------------------------------------- #
    # POST /scheduler/tasks
    # ------------------------------------------------------------------- #

    @app.post("/scheduler/tasks", status_code=201, dependencies=[Depends(require_auth)])
    async def create_task(
        payload: TaskCreate, current_user=Depends(require_auth)
    ) -> dict[str, Any]:
        """Create a task and register it with the live scheduler.

        Auto-creates a dedicated session when ``session_id`` is omitted, so the
        agent transcript for every fire is preserved and replayable.
        """
        assert _store is not None and _service is not None  # noqa: S101

        # Validate the schedule spec eagerly so a 400 reaches the user before
        # any DB row is written.
        try:
            payload.schedule.resolved_cron()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Resolve / create the dedicated session.
        svc = _default_session_factory(current_user.id)
        session_id = payload.session_id
        if session_id:
            # Verify ownership: the session must exist under this user.
            if svc is not None:
                existing = svc.get_session(session_id)
                if existing is None:
                    raise HTTPException(
                        status_code=404, detail=f"session {session_id!r} not found"
                    )
        else:
            if svc is None:
                raise HTTPException(
                    status_code=503,
                    detail="session runtime is disabled; cannot create a task session",
                )
            title = f"Scheduled: {payload.title}"
            session = svc.create_session(title=title)
            session_id = session.session_id

        task = ScheduledTask(
            id=_new_uuid(),
            user_id=current_user.id,
            title=payload.title,
            prompt=payload.prompt,
            schedule_type=payload.schedule.type,
            schedule_preset=payload.schedule.preset,
            cron_expr=payload.schedule.cron,
            timezone=payload.schedule.timezone,
            session_id=session_id,
            enabled=True,
            on_overlap=payload.on_overlap,
            notify_enabled=payload.notify_enabled,
            notify_emails=payload.notify_emails,
            last_status="idle",
            run_count=0,
        )
        _store.create_task(task)
        _service.register_task(task)
        return {"status": "ok", "task": _task_to_out(task).model_dump()}

    # ------------------------------------------------------------------- #
    # GET /scheduler/tasks/{id}
    # ------------------------------------------------------------------- #

    @app.get(
        "/scheduler/tasks/{task_id}",
        dependencies=[Depends(require_auth)],
    )
    async def get_task(task_id: str, current_user=Depends(require_auth)) -> dict[str, Any]:
        """Return one task (with live next_run_at) — 404 for non-owners."""
        assert _store is not None  # noqa: S101
        task = _store.get_task(task_id, current_user.id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return {"status": "ok", "task": _task_to_out(task).model_dump()}

    # ------------------------------------------------------------------- #
    # PATCH /scheduler/tasks/{id}
    # ------------------------------------------------------------------- #

    @app.patch(
        "/scheduler/tasks/{task_id}",
        dependencies=[Depends(require_auth)],
    )
    async def update_task(
        task_id: str, payload: TaskUpdate, current_user=Depends(require_auth)
    ) -> dict[str, Any]:
        """Update task fields; schedule change refreshes the live scheduler job."""
        assert _store is not None and _service is not None  # noqa: S101
        task = _store.get_task(task_id, current_user.id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")

        if payload.title is not None:
            task.title = payload.title
        if payload.prompt is not None:
            task.prompt = payload.prompt
        if payload.enabled is not None:
            task.enabled = payload.enabled
        if payload.on_overlap is not None:
            task.on_overlap = payload.on_overlap
        if payload.notify_enabled is not None:
            task.notify_enabled = payload.notify_enabled
        if payload.notify_emails is not None:
            task.notify_emails = payload.notify_emails
        if payload.timezone is not None and payload.schedule is None:
            task.timezone = payload.timezone
        if payload.schedule is not None:
            try:
                payload.schedule.resolved_cron()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            task.schedule_type = payload.schedule.type
            task.schedule_preset = payload.schedule.preset
            task.cron_expr = payload.schedule.cron
            task.timezone = payload.schedule.timezone

        task.updated_at = _utcnow()
        _store.update_task(task)
        # Always re-register: the schedule may have changed, or the task may
        # have been toggled off and needs to be removed from the live loop.
        _service.register_task(task)
        return {"status": "ok", "task": _task_to_out(task).model_dump()}

    # ------------------------------------------------------------------- #
    # DELETE /scheduler/tasks/{id}
    # ------------------------------------------------------------------- #

    @app.delete(
        "/scheduler/tasks/{task_id}",
        dependencies=[Depends(require_auth)],
    )
    async def delete_task(task_id: str, current_user=Depends(require_auth)) -> dict[str, Any]:
        """Delete a task and remove its job from the live scheduler."""
        assert _store is not None and _service is not None  # noqa: S101
        existed = _store.delete_task(task_id, current_user.id)
        if not existed:
            raise HTTPException(status_code=404, detail="task not found")
        _service.unregister_task(task_id)
        return {"status": "ok", "deleted": task_id}

    # ------------------------------------------------------------------- #
    # POST /scheduler/tasks/{id}/run  (manual trigger)
    # ------------------------------------------------------------------- #

    class RunResponse(BaseModel):
        status: str
        attempt_id: str | None = None
        reason: str | None = None

    @app.post(
        "/scheduler/tasks/{task_id}/run",
        dependencies=[Depends(require_auth)],
    )
    async def run_task_now(
        task_id: str, current_user=Depends(require_auth)
    ) -> dict[str, Any]:
        """Fire a task immediately, bypassing the overlap lock.

        Returns the runner's outcome; the agent transcript lands in the task's
        dedicated session and is visible via the existing session SSE.
        """
        assert _store is not None and _service is not None  # noqa: S101
        # Owner check — non-owners see 404.
        task = _store.get_task(task_id, current_user.id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        result = await _service.trigger_now(task_id)
        return {"status": "ok", "result": result}

    # ------------------------------------------------------------------- #
    # POST /scheduler/tasks/{id}/toggle
    # ------------------------------------------------------------------- #

    @app.post(
        "/scheduler/tasks/{task_id}/toggle",
        dependencies=[Depends(require_auth)],
    )
    async def toggle_task(
        task_id: str, current_user=Depends(require_auth)
    ) -> dict[str, Any]:
        """Flip a task's ``enabled`` flag and refresh the live scheduler job."""
        assert _store is not None and _service is not None  # noqa: S101
        task = _store.get_task(task_id, current_user.id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        task.enabled = not task.enabled
        task.updated_at = _utcnow()
        _store.update_task(task)
        _service.register_task(task)
        return {"status": "ok", "task": _task_to_out(task).model_dump()}


# --------------------------------------------------------------------------- #
# Service lifecycle hooks (called by api_server lifespan)
# --------------------------------------------------------------------------- #


def start_service() -> None:
    """Start the scheduler service. Called from the api_server startup hook."""
    if _service is not None:
        _service.start()


async def stop_service() -> None:
    """Stop the scheduler service. Called from the api_server shutdown hook."""
    if _service is not None:
        await _service.stop()
