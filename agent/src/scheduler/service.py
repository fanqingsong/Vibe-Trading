"""Global scheduler lifecycle for the generic scheduled-task subsystem.

Owns the single process-wide :class:`Scheduler` instance and the
:class:`PromptRunner` that turns each tick into an agent run. Started from the
FastAPI lifespan / startup hook; on (re)start it bootstraps every enabled task
back into the scheduler (resume-via-recompute, mirroring the live-runtime
pattern).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from src.live.runtime.scheduler import Job, Scheduler
from src.scheduler.cron import next_run_ms
from src.scheduler.runner import PromptRunner, SessionFactory
from src.scheduler.store import ScheduledTaskStore

logger = logging.getLogger(__name__)


class SchedulerService:
    """Process-wide singleton wiring tasks → scheduler → agent.

    Attributes:
        store: Task persistence layer.
        runner: The ``on_fire`` callback handler.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        store: ScheduledTaskStore | None = None,
        scheduler: Scheduler | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            session_factory: ``(user_id) -> SessionService | None``. Bound by
                the host ``api_server`` to its per-user factory; kept as a
                callable here to avoid a circular import.
            store: Task store. Defaults to a new :class:`ScheduledTaskStore`.
            scheduler: Optional injected scheduler (tests). When ``None`` a
                real one is constructed lazily in :meth:`start` so we bind to
                the running event loop, not the import-time one.
        """
        self.store = store or ScheduledTaskStore()
        self._session_factory = session_factory
        self._scheduler: Scheduler | None = scheduler
        self._runner: PromptRunner | None = None
        self._started = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the scheduler and bootstrap every enabled task into it.

        Idempotent: a second call is a no-op. Safe to invoke from the FastAPI
        startup hook even when no tasks exist.
        """
        if self._started:
            return
        if self._scheduler is None:
            self._scheduler = Scheduler(on_fire=self._on_fire_dispatch)
        # Wire the scheduler into the runner so it can re-arm jobs.
        self._runner = PromptRunner(
            store=self.store,
            session_factory=self._session_factory,
            scheduler=self._scheduler,
        )
        self._bootstrap_jobs()
        self._scheduler.start()
        self._started = True
        logger.info("scheduler service started")

    async def stop(self) -> None:
        """Stop the scheduler (idempotent)."""
        if not self._started or self._scheduler is None:
            return
        await self._scheduler.stop()
        self._started = False
        logger.info("scheduler service stopped")

    @property
    def is_running(self) -> bool:
        """Return whether the service is currently running."""
        return self._started

    # ------------------------------------------------------------------ #
    # Job bootstrap + (re)registration
    # ------------------------------------------------------------------ #

    def _bootstrap_jobs(self) -> None:
        """Recompute every enabled task's next-fire and register it.

        Called on :meth:`start`. A task whose schedule cannot be parsed is
        logged and skipped (its DB row stays; the user can fix it from the UI).
        """
        from datetime import datetime, timezone

        tasks = self.store.get_all_enabled_tasks()
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        registered = 0
        for task in tasks:
            try:
                next_ms = next_run_ms(
                    schedule_type=task.schedule_type,
                    preset=task.schedule_preset,
                    cron_expr=task.cron_expr,
                    timezone_name=task.timezone or "Asia/Shanghai",
                    now_ms=now_ms,
                )
            except Exception:  # noqa: BLE001 — one bad task must not block others
                logger.exception(
                    "failed to schedule task %s during bootstrap; skipping",
                    task.id,
                )
                continue
            self._scheduler.add_job(  # type: ignore[union-attr]
                Job(
                    id=f"sched-{task.id}",
                    next_run_at=next_ms,
                    schedule="once",
                    payload={"task_id": task.id},
                )
            )
            registered += 1
        logger.info("scheduler bootstrap registered %d/%d tasks", registered, len(tasks))

    def register_task(self, task: Any) -> None:
        """Register (or refresh) a task's next-fire job in the live scheduler.

        Called by the route layer after create / update / toggle-on so the
        new cadence takes effect without a service restart.

        Args:
            task: The :class:`ScheduledTask` to register.
        """
        if not self._started or self._scheduler is None:
            return
        from datetime import datetime, timezone

        job_id = f"sched-{task.id}"
        self._scheduler.remove_job(job_id)
        if not task.enabled:
            return
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        try:
            next_ms = next_run_ms(
                schedule_type=task.schedule_type,
                preset=task.schedule_preset,
                cron_expr=task.cron_expr,
                timezone_name=task.timezone or "Asia/Shanghai",
                now_ms=now_ms,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to compute next_run_at for task %s", task.id)
            return
        self._scheduler.add_job(
            Job(
                id=job_id,
                next_run_at=next_ms,
                schedule="once",
                payload={"task_id": task.id},
            )
        )

    def unregister_task(self, task_id: str) -> None:
        """Remove a task's job from the live scheduler.

        Args:
            task_id: The task id whose job should be removed.
        """
        if not self._started or self._scheduler is None:
            return
        self._scheduler.remove_job(f"sched-{task_id}")

    # ------------------------------------------------------------------ #
    # Manual trigger
    # ------------------------------------------------------------------ #

    async def trigger_now(self, task_id: str) -> dict[str, Any]:
        """Fire a task immediately, bypassing the scheduler.

        Args:
            task_id: The task to fire.

        Returns:
            The runner's status dict.
        """
        if self._runner is None:
            # Allow manual trigger even when the loop isn't running yet — the
            # runner can operate standalone.
            self._runner = PromptRunner(
                store=self.store,
                session_factory=self._session_factory,
                scheduler=self._scheduler,
            )
        return await self._runner.run_task_now(task_id)

    # ------------------------------------------------------------------ #
    # Scheduler dispatch
    # ------------------------------------------------------------------ #

    async def _on_fire_dispatch(self, job: Any) -> None:
        """Adapter from :class:`Scheduler` to :class:`PromptRunner.on_fire`."""
        if self._runner is None:
            logger.warning("scheduler fired before runner was initialized; dropping job %s", getattr(job, "id", "?"))
            return
        await self._runner.on_fire(job)

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, Any]:
        """Return a health snapshot of the scheduler service."""
        jobs = self._scheduler.jobs() if self._scheduler else []
        return {
            "running": self._started,
            "job_count": len(jobs),
            "next_fire_at": min((j.next_run_at for j in jobs), default=None),
        }
