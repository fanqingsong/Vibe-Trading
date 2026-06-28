"""Unit tests for the PromptRunner / SchedulerService execution path.

Uses the in-memory store fallback (no DB) and a fake SessionService so the
full fire → invoke → record → rearm chain is exercised without an agent.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

import pytest

from src.db.models import ScheduledTask
from src.live.runtime.scheduler import Scheduler
from src.scheduler.runner import PromptRunner
from src.scheduler.service import SchedulerService
from src.scheduler.store import ScheduledTaskStore


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class FakeSession:
    """Stand-in for SessionService that records send_message calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    async def send_message(self, *, session_id: str, content: str) -> Mapping[str, Any]:
        self.calls.append((session_id, content))
        if self._fail:
            raise RuntimeError("boom")
        return {"message_id": "m-1", "attempt_id": "att-1"}


def _make_task(
    *,
    id: str = "t1",
    user_id: str = "u1",
    preset: str = "daily_0930",
    enabled: bool = True,
    on_overlap: str = "skip",
) -> ScheduledTask:
    return ScheduledTask(
        id=id,
        user_id=user_id,
        title="Test task",
        prompt="Hello agent",
        schedule_type="preset",
        schedule_preset=preset,
        cron_expr=None,
        timezone="UTC",
        session_id="sess-1",
        enabled=enabled,
        on_overlap=on_overlap,
        last_status="idle",
        run_count=0,
    )


# --------------------------------------------------------------------------- #
# PromptRunner — manual trigger
# --------------------------------------------------------------------------- #


async def test_runner_manual_trigger_invokes_session_service_and_records_success() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task())
    fake = FakeSession()
    runner = PromptRunner(store=store, session_factory=lambda uid: fake)

    result = await runner.run_task_now("t1")

    assert result == {"status": "success", "attempt_id": "att-1"}
    assert fake.calls == [("sess-1", "Hello agent")]
    task = store.get_task("t1", "u1")
    assert task.last_status == "success"
    assert task.last_attempt_id == "att-1"
    assert task.run_count == 1
    assert task.last_error is None


async def test_runner_records_failure_and_keeps_error_string() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task())
    fake = FakeSession(fail=True)
    runner = PromptRunner(store=store, session_factory=lambda uid: fake)

    result = await runner.run_task_now("t1")

    assert result["status"] == "failed"
    task = store.get_task("t1", "u1")
    assert task.last_status == "failed"
    assert "boom" in (task.last_error or "")
    assert task.run_count == 1


async def test_runner_skips_when_session_factory_returns_none() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task())
    runner = PromptRunner(store=store, session_factory=lambda uid: None)

    result = await runner.run_task_now("t1")

    assert result["status"] == "failed"
    task = store.get_task("t1", "u1")
    assert "disabled" in (task.last_error or "")


# --------------------------------------------------------------------------- #
# PromptRunner — overlap gate
# --------------------------------------------------------------------------- #


async def test_runner_skip_overlap_policy_drops_second_fire_while_first_running() -> None:
    """When a previous run is still in-flight, a scheduler-driven fire is skipped.

    Simulates the in-flight condition by pre-seeding the runner's ``_inflight``
    set (the same set the runner checks), then calling the internal
    ``_run_task`` path the scheduler uses (``force=False``). This avoids flaky
    timing with concurrent asyncio tasks while still exercising the gate.
    """
    store = ScheduledTaskStore()
    store.create_task(_make_task(on_overlap="skip"))
    fake = FakeSession()
    runner = PromptRunner(store=store, session_factory=lambda uid: fake)

    # Pretend a previous fire is still running.
    runner._inflight.add("t1")  # noqa: SLF001

    result = await runner._run_task("t1", force=False)  # noqa: SLF001

    assert result["status"] == "skipped"
    assert result["reason"] == "overlap"
    # The session service was never reached.
    assert fake.calls == []


async def test_runner_force_bypasses_overlap_gate() -> None:
    """run_task_now is the manual-trigger path and ignores the gate (admin override)."""
    store = ScheduledTaskStore()
    store.create_task(_make_task(on_overlap="skip"))
    fake = FakeSession()
    runner = PromptRunner(store=store, session_factory=lambda uid: fake)

    # Force=True is what run_task_now itself uses, so two back-to-back manual
    # triggers should both succeed even though the first is logically still
    # finishing — the gate is only consulted on scheduler-driven fires, which
    # go through _run_task(force=False).
    r1 = await runner.run_task_now("t1")
    r2 = await runner.run_task_now("t1")
    assert r1["status"] == "success"
    assert r2["status"] == "success"


# --------------------------------------------------------------------------- #
# PromptRunner — disabled task
# --------------------------------------------------------------------------- #


async def test_runner_skips_disabled_task() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task(enabled=False))
    fake = FakeSession()
    runner = PromptRunner(store=store, session_factory=lambda uid: fake)

    result = await runner.run_task_now("t1")

    assert result["status"] == "skipped"
    assert fake.calls == []  # never reached the session service


async def test_runner_skips_unknown_task() -> None:
    store = ScheduledTaskStore()
    runner = PromptRunner(store=store, session_factory=lambda uid: FakeSession())

    result = await runner.run_task_now("does-not-exist")

    assert result["status"] == "skipped"


# --------------------------------------------------------------------------- #
# SchedulerService — bootstrap + lifecycle
# --------------------------------------------------------------------------- #


async def test_service_bootstrap_registers_every_enabled_task() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task(id="t1", preset="hourly"))
    store.create_task(_make_task(id="t2", preset="daily_0930", enabled=False))
    fake = FakeSession()
    svc = SchedulerService(session_factory=lambda uid: fake, store=store)

    svc.start()
    try:
        # The scheduler should hold exactly one job (the enabled task).
        jobs = svc._scheduler.jobs() if svc._scheduler else []  # noqa: SLF001
        assert len(jobs) == 1
        assert jobs[0].payload == {"task_id": "t1"}
    finally:
        await svc.stop()


async def test_service_register_and_unregister_task_keeps_scheduler_in_sync() -> None:
    store = ScheduledTaskStore()
    fake = FakeSession()
    svc = SchedulerService(session_factory=lambda uid: fake, store=store)

    svc.start()
    try:
        svc.register_task(_make_task(id="t-new", preset="hourly"))
        jobs = svc._scheduler.jobs() if svc._scheduler else []  # noqa: SLF001
        assert any(j.id == "sched-t-new" for j in jobs)

        svc.unregister_task("t-new")
        jobs = svc._scheduler.jobs() if svc._scheduler else []  # noqa: SLF001
        assert not any(j.id == "sched-t-new" for j in jobs)
    finally:
        await svc.stop()


async def test_service_trigger_now_runs_the_task_through_the_session_service() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task(id="t1"))
    fake = FakeSession()
    svc = SchedulerService(session_factory=lambda uid: fake, store=store)

    # trigger_now works even before start() — the runner is built lazily.
    result = await svc.trigger_now("t1")
    assert result["status"] == "success"
    assert fake.calls == [("sess-1", "Hello agent")]


# --------------------------------------------------------------------------- #
# SchedulerService — real Scheduler integration (end-to-end fire)
# --------------------------------------------------------------------------- #


async def test_service_real_scheduler_fires_prompt_on_next_tick() -> None:
    """With a real Scheduler, a due job fires its prompt through the runner.

    Uses a fake clock (``now_fn=lambda: 10_000``) so the due job fires on the
    first tick with no real wall-clock waiting. We bypass the service's
    bootstrap (which would compute a far-future next_run_at) and register an
    already-due job directly, then start the scheduler loop.
    """
    from src.live.runtime.scheduler import Job, Scheduler

    store = ScheduledTaskStore()
    store.create_task(_make_task(id="t1", preset="every_minute"))
    fake = FakeSession()

    # Build the service first (without a scheduler), then construct the
    # Scheduler with the service's dispatch as on_fire.
    svc = SchedulerService(session_factory=lambda uid: fake, store=store)
    sched = Scheduler(svc._on_fire_dispatch, now_fn=lambda: 10_000)  # noqa: SLF001
    svc._scheduler = sched  # noqa: SLF001

    # Register an already-due job (next_run_at=0 < now_fn=10_000) before start.
    sched.add_job(
        Job(id="sched-t1", next_run_at=0, schedule="once", payload={"task_id": "t1"})
    )

    # Start the runner manually (NOT svc.start() which would re-bootstrap and
    # overwrite our due job with a far-future one).
    from src.scheduler.runner import PromptRunner

    svc._runner = PromptRunner(
        store=store, session_factory=lambda uid: fake, scheduler=sched
    )
    svc._started = True
    sched.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0)
            if fake.calls:
                break
        assert fake.calls == [("sess-1", "Hello agent")]
    finally:
        await svc.stop()
