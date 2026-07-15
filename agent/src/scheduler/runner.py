"""The ``on_fire`` callback that turns a scheduler tick into an agent run.

This is the lightweight analog of :class:`src.live.runtime.runner.LiveRunner`,
with all mandate / broker / reconcile coupling stripped. Per fire, it:

1. Loads the :class:`ScheduledTask` row from the store (by id in the job payload).
2. Checks ``enabled`` (cheap skip when the user paused the task after the job
   was registered) and the per-task overlap lock.
3. Invokes the agent **directly** with the task prompt (no Session / chat
   transcript — scheduled tasks are not conversations).
4. Records ``last_run_at`` / ``last_status`` / ``last_error`` / ``last_summary``
   / ``last_run_id`` on the task row.
5. Optionally emails the owner when ``notify_enabled`` is set.
6. Re-arms the scheduler job with the next ``next_run_at`` so the cadence holds.

Every external dependency (store, agent runner, clock) is injectable so the
runner is unit-testable with no live agent or broker.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol

from src.scheduler.cron import next_run_ms
from src.scheduler.store import ScheduledTaskStore

logger = logging.getLogger(__name__)

#: Status codes written to ``ScheduledTask.last_status``.
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

#: Max chars persisted on ``ScheduledTask.last_summary``.
_SUMMARY_MAX = 8000


class _SchedulerLike(Protocol):
    """Minimal ``Scheduler`` view the runner relies on for re-arming jobs."""

    def add_job(self, job: Any) -> None: ...

    def remove_job(self, job_id: str) -> bool: ...


#: ``(prompt, task_id) -> AgentLoop result dict``. Defaults to
#: :func:`src.scheduler.executor.run_scheduled_prompt`.
AgentRunner = Callable[[str, str], Awaitable[Mapping[str, Any]]]


def _now_ms() -> int:
    """Return the current wall-clock time in epoch milliseconds (UTC)."""
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _default_agent_runner(prompt: str, task_id: str) -> Awaitable[Mapping[str, Any]]:
    from src.scheduler.executor import run_scheduled_prompt

    return run_scheduled_prompt(prompt, task_id=task_id)


class PromptRunner:
    """Fire callback that runs a scheduled task's prompt through the agent.

    The runner is safe to call concurrently from the scheduler — every task has
    its own in-flight lock keyed by ``task_id``, and the overlap policy decides
    what happens when a fire lands while a previous run is still active.

    Attributes:
        store: Task persistence layer.
    """

    def __init__(
        self,
        *,
        store: ScheduledTaskStore,
        agent_runner: AgentRunner | None = None,
        scheduler: _SchedulerLike | None = None,
        now_ms_fn: Callable[[], int] = _now_ms,
    ) -> None:
        """Initialize the runner.

        Args:
            store: Task store for loading/updating :class:`ScheduledTask` rows.
            agent_runner: Async ``(prompt, task_id) -> result`` callable.
                Defaults to the direct agent executor (no Session).
            scheduler: The scheduler, used to re-arm the next-fire job after a
                tick. May be ``None`` in tests that only exercise one fire.
            now_ms_fn: Injectable epoch-ms clock for determinism.
        """
        self._store = store
        self._agent_runner: AgentRunner = agent_runner or _default_agent_runner
        self._scheduler = scheduler
        self._now_ms = now_ms_fn
        #: task_id → currently-running fire. Guarded by ``_inflight_lock``.
        self._inflight: set[str] = set()
        self._inflight_lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Scheduler callback
    # ------------------------------------------------------------------ #

    async def on_fire(self, job: Any) -> None:
        """Scheduler ``on_fire`` callback.

        Args:
            job: A :class:`src.live.runtime.scheduler.Job` whose ``payload``
                carries ``{"task_id": "..."}``.
        """
        payload = getattr(job, "payload", None) or {}
        task_id = payload.get("task_id")
        if not task_id:
            logger.warning("scheduler job %s has no task_id payload; skipping", getattr(job, "id", "?"))
            return

        await self._run_task(task_id)

    # ------------------------------------------------------------------ #
    # Core execution
    # ------------------------------------------------------------------ #

    async def run_task_now(self, task_id: str) -> dict[str, Any]:
        """Run a task immediately, ignoring the overlap lock (manual trigger).

        Used by the ``POST /scheduler/tasks/{id}/run`` admin endpoint so a user
        can always force a fire even if the previous run is still winding down.

        Args:
            task_id: The task to fire.

        Returns:
            A small status dict ``{"status": "...", "reason": "..."}``.
        """
        return await self._run_task(task_id, force=True)

    async def _run_task(self, task_id: str, *, force: bool = False) -> dict[str, Any]:
        """Load the task, check overlap, invoke the agent, record the outcome."""
        tasks = self._store.get_all_enabled_tasks()
        task = next((t for t in tasks if t.id == task_id), None)
        if task is None:
            # Either the task was deleted, disabled, or never existed.
            logger.info("scheduled task %s not found or disabled; skipping", task_id)
            return {"status": STATUS_SKIPPED, "reason": "not found or disabled"}

        if not task.enabled:
            self._mark_skipped(task, reason="task disabled")
            return {"status": STATUS_SKIPPED, "reason": "disabled"}

        # Overlap gate.
        async with self._inflight_lock:
            if task_id in self._inflight and not force:
                if task.on_overlap == "skip":
                    self._mark_skipped(task, reason="previous run still active")
                    return {"status": STATUS_SKIPPED, "reason": "overlap"}
                # queue / replace are reserved; v1 treats them as skip too.
                self._mark_skipped(task, reason=f"overlap policy={task.on_overlap} not yet implemented")
                return {"status": STATUS_SKIPPED, "reason": "overlap policy unsupported"}
            self._inflight.add(task_id)

        try:
            return await self._invoke_and_record(task)
        finally:
            async with self._inflight_lock:
                self._inflight.discard(task_id)
            # Always re-arm the next fire (even on failure) so a transient
            # error doesn't permanently silence the cadence.
            self._rearm(task)

    async def _invoke_and_record(self, task: Any) -> dict[str, Any]:
        """Run the prompt through the agent and persist the outcome on the task."""
        self._mark_status(task, STATUS_RUNNING)

        try:
            result = await self._agent_runner(task.prompt, task.id)
        except Exception as exc:  # noqa: BLE001 — never crash the scheduler loop
            logger.exception("scheduled task %s invocation failed", task.id)
            self._mark_failed(task, str(exc)[:500])
            self._maybe_notify(task, status="failed", summary="", error=str(exc), run_id="")
            return {"status": STATUS_FAILED, "reason": str(exc)[:200]}

        status = ""
        content = ""
        run_id = ""
        reason = ""
        if isinstance(result, Mapping):
            status = str(result.get("status") or "")
            content = str(result.get("content") or "")
            run_id = str(result.get("run_id") or "")
            reason = str(result.get("reason") or "")

        if status == "success":
            self._mark_success(task, run_id=run_id, summary=content)
            self._maybe_notify(task, status="completed", summary=content, error="", run_id=run_id)
            return {"status": STATUS_SUCCESS, "run_id": run_id}

        err = reason or f"agent status={status or 'unknown'}"
        self._mark_failed(task, err[:500], run_id=run_id, summary=content)
        self._maybe_notify(task, status="failed", summary=content, error=err, run_id=run_id)
        return {"status": STATUS_FAILED, "reason": err[:200], "run_id": run_id}

    # ------------------------------------------------------------------ #
    # Status writers
    # ------------------------------------------------------------------ #

    def _mark_status(self, task: Any, status: str) -> None:
        task.last_status = status
        if status == STATUS_RUNNING:
            # Clear stale outcome from a previous fire so the UI doesn't show
            # "Running" alongside an old error / summary.
            task.last_error = None
        task.last_run_at = datetime.now(tz=timezone.utc)
        self._store.update_task(task)

    def _mark_success(self, task: Any, *, run_id: str, summary: str) -> None:
        task.last_status = STATUS_SUCCESS
        task.last_error = None
        task.last_run_id = run_id or None
        task.last_summary = (summary or "")[:_SUMMARY_MAX] or None
        # Legacy column kept in sync for older UI/API clients.
        task.last_attempt_id = run_id or None
        task.run_count = (task.run_count or 0) + 1
        task.last_run_at = datetime.now(tz=timezone.utc)
        self._store.update_task(task)

    def _mark_failed(
        self, task: Any, reason: str, *, run_id: str = "", summary: str = ""
    ) -> None:
        task.last_status = STATUS_FAILED
        task.last_error = reason
        if run_id:
            task.last_run_id = run_id
            task.last_attempt_id = run_id
        if summary:
            task.last_summary = summary[:_SUMMARY_MAX]
        task.run_count = (task.run_count or 0) + 1
        task.last_run_at = datetime.now(tz=timezone.utc)
        self._store.update_task(task)

    def _mark_skipped(self, task: Any, *, reason: str) -> None:
        task.last_status = STATUS_SKIPPED
        task.last_error = reason
        task.last_run_at = datetime.now(tz=timezone.utc)
        self._store.update_task(task)

    # ------------------------------------------------------------------ #
    # Notify
    # ------------------------------------------------------------------ #

    def _maybe_notify(
        self,
        task: Any,
        *,
        status: str,
        summary: str,
        error: str,
        run_id: str,
    ) -> None:
        """Email the owner when the task has notify_enabled. Best-effort."""
        if not getattr(task, "notify_enabled", False):
            return
        try:
            from src.notify.dispatcher import EVENT_SCHEDULER_TASK_COMPLETED, fire_and_forget

            owner_email = self._lookup_owner_email(task.user_id)
            recipients_override = self._parse_notify_emails(task.notify_emails)
            fire_and_forget(
                EVENT_SCHEDULER_TASK_COMPLETED,
                {
                    "task_id": task.id,
                    "title": task.title,
                    "prompt": task.prompt,
                    "summary": summary,
                    "error": error,
                    "status": status,
                    "run_id": run_id,
                    "attempt_id": run_id,  # legacy key for notify templates
                    "owner_email": owner_email,
                    "recipients_override": recipients_override,
                    "web_url": "/scheduler",
                },
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "scheduled-task email notification failed for task %s",
                task.id,
                exc_info=True,
            )

    @staticmethod
    def _lookup_owner_email(user_id: str | None) -> str:
        if not user_id:
            return ""
        try:
            from src.db.base import get_session as _db_session
            from src.db.base import is_db_enabled
            from src.db.models import User

            if not is_db_enabled():
                return ""
            with _db_session() as s:  # type: ignore[assignment]
                if s is None:
                    return ""
                row = s.query(User).filter(User.id == user_id).one_or_none()
                return str(row.email) if row is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _parse_notify_emails(raw: str | None) -> list[str]:
        if not raw:
            return []
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]

    # ------------------------------------------------------------------ #
    # Job re-arm
    # ------------------------------------------------------------------ #

    def _rearm(self, task: Any) -> None:
        """Re-register the next-fire job so the cadence continues.

        Builds a fresh :class:`Job` with ``next_run_at`` computed from the
        task's schedule spec and the current wall clock. The scheduler is
        expected to have already removed the just-fired job (one-shot) or
        advanced its ``next_run_at`` (recurring). For cron-driven tasks we
        always replace the job with a freshly computed next-fire to keep the
        cadence aligned to wall-clock time rather than a fixed interval.
        """
        if self._scheduler is None:
            return
        try:
            next_ms = next_run_ms(
                schedule_type=task.schedule_type,
                preset=task.schedule_preset,
                cron_expr=task.cron_expr,
                timezone_name=task.timezone or "Asia/Shanghai",
                now_ms=self._now_ms(),
            )
        except Exception:  # noqa: BLE001 — a bad schedule must not crash the runner
            logger.exception(
                "failed to compute next_run_at for task %s; cadence stopped",
                task.id,
            )
            return

        from src.live.runtime.scheduler import Job

        job_id = f"sched-{task.id}"
        # remove_job is idempotent: returns False if the job was already gone.
        self._scheduler.remove_job(job_id)
        self._scheduler.add_job(
            Job(
                id=job_id,
                next_run_at=next_ms,
                schedule="once",  # we manage recurrence ourselves via re-arm
                payload={"task_id": task.id},
            )
        )
