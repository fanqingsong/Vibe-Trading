"""Unit tests for the PromptRunner / SchedulerService execution path.

Uses the in-memory store fallback (no DB) and a fake agent runner so the
full fire → invoke → record → rearm chain is exercised without a live agent.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

import pytest

from src.db.models import ScheduledTask
from src.live.runtime.scheduler import Job, Scheduler
from src.scheduler.runner import PromptRunner
from src.scheduler.service import SchedulerService
from src.scheduler.store import ScheduledTaskStore


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class FakeAgent:
    """Stand-in for the direct agent executor that records run calls."""

    def __init__(self, *, fail: bool = False, status: str = "success") -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail
        self._status = status

    async def __call__(self, prompt: str, task_id: str) -> Mapping[str, Any]:
        self.calls.append((prompt, task_id))
        if self._fail:
            raise RuntimeError("boom")
        return {
            "status": self._status,
            "content": "agent reply",
            "run_id": "run-1",
            "reason": "" if self._status == "success" else "agent failed",
        }


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
        session_id="",
        enabled=enabled,
        on_overlap=on_overlap,
        last_status="idle",
        run_count=0,
    )


# --------------------------------------------------------------------------- #
# PromptRunner — manual trigger
# --------------------------------------------------------------------------- #


async def test_runner_manual_trigger_invokes_agent_and_records_success() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task())
    fake = FakeAgent()
    runner = PromptRunner(store=store, agent_runner=fake)

    result = await runner.run_task_now("t1")

    assert result == {"status": "success", "run_id": "run-1"}
    assert fake.calls == [("Hello agent", "t1")]
    task = store.get_task("t1", "u1")
    assert task.last_status == "success"
    assert task.last_run_id == "run-1"
    assert task.last_summary == "agent reply"
    assert task.run_count == 1
    assert task.last_error is None


async def test_runner_records_failure_and_keeps_error_string() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task())
    fake = FakeAgent(fail=True)
    runner = PromptRunner(store=store, agent_runner=fake)

    result = await runner.run_task_now("t1")

    assert result["status"] == "failed"
    task = store.get_task("t1", "u1")
    assert task.last_status == "failed"
    assert "boom" in (task.last_error or "")
    assert task.run_count == 1


async def test_runner_records_agent_non_success_status() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task())
    fake = FakeAgent(status="failed")
    runner = PromptRunner(store=store, agent_runner=fake)

    result = await runner.run_task_now("t1")

    assert result["status"] == "failed"
    task = store.get_task("t1", "u1")
    assert task.last_status == "failed"
    assert "agent failed" in (task.last_error or "")


# --------------------------------------------------------------------------- #
# PromptRunner — overlap gate
# --------------------------------------------------------------------------- #


async def test_runner_skip_overlap_policy_drops_second_fire_while_first_running() -> None:
    """When a previous run is still in-flight, a scheduler-driven fire is skipped."""
    store = ScheduledTaskStore()
    store.create_task(_make_task(on_overlap="skip"))
    fake = FakeAgent()
    runner = PromptRunner(store=store, agent_runner=fake)

    runner._inflight.add("t1")  # noqa: SLF001

    result = await runner._run_task("t1", force=False)  # noqa: SLF001

    assert result["status"] == "skipped"
    assert result["reason"] == "overlap"
    assert fake.calls == []


async def test_runner_force_bypasses_overlap_gate() -> None:
    """run_task_now is the manual-trigger path and ignores the gate."""
    store = ScheduledTaskStore()
    store.create_task(_make_task(on_overlap="skip"))
    fake = FakeAgent()
    runner = PromptRunner(store=store, agent_runner=fake)

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
    fake = FakeAgent()
    runner = PromptRunner(store=store, agent_runner=fake)

    result = await runner.run_task_now("t1")

    assert result["status"] == "skipped"
    assert fake.calls == []


async def test_runner_skips_unknown_task() -> None:
    store = ScheduledTaskStore()
    runner = PromptRunner(store=store, agent_runner=FakeAgent())

    result = await runner.run_task_now("does-not-exist")

    assert result["status"] == "skipped"


async def test_store_recover_stale_running_tasks() -> None:
    store = ScheduledTaskStore()
    task = _make_task()
    task.last_status = "running"
    task.last_error = None
    store.create_task(task)

    n = store.recover_stale_running_tasks(reason="interrupted: test")
    assert n == 1
    row = store.get_task("t1", "u1")
    assert row.last_status == "failed"
    assert "interrupted" in (row.last_error or "")


# --------------------------------------------------------------------------- #
# PromptRunner — notify
# --------------------------------------------------------------------------- #


async def test_runner_notifies_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ScheduledTaskStore()
    task = _make_task()
    task.notify_enabled = True
    store.create_task(task)
    fake = FakeAgent()
    runner = PromptRunner(store=store, agent_runner=fake)

    captured: list[tuple[str, dict]] = []

    def _faf(event_type: str, data: dict) -> None:
        captured.append((event_type, data))

    monkeypatch.setattr("src.notify.dispatcher.fire_and_forget", _faf)
    monkeypatch.setattr(PromptRunner, "_lookup_owner_email", staticmethod(lambda uid: "o@x.com"))

    await runner.run_task_now("t1")

    assert len(captured) == 1
    event_type, data = captured[0]
    assert event_type == "scheduler.task.completed"
    assert data["summary"] == "agent reply"
    assert data["status"] == "completed"
    assert data["owner_email"] == "o@x.com"


async def test_runner_skips_notify_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task())
    fake = FakeAgent()
    runner = PromptRunner(store=store, agent_runner=fake)

    called = []
    monkeypatch.setattr(
        "src.notify.dispatcher.fire_and_forget",
        lambda *a, **k: called.append(True),
    )

    await runner.run_task_now("t1")
    assert called == []


# --------------------------------------------------------------------------- #
# SchedulerService — bootstrap + lifecycle
# --------------------------------------------------------------------------- #


async def test_service_bootstrap_registers_every_enabled_task() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task(id="t1", preset="hourly"))
    store.create_task(_make_task(id="t2", preset="daily_0930", enabled=False))
    fake = FakeAgent()
    svc = SchedulerService(agent_runner=fake, store=store)

    svc.start()
    try:
        jobs = svc._scheduler.jobs() if svc._scheduler else []  # noqa: SLF001
        assert len(jobs) == 1
        assert jobs[0].payload == {"task_id": "t1"}
    finally:
        await svc.stop()


async def test_service_register_and_unregister_task_keeps_scheduler_in_sync() -> None:
    store = ScheduledTaskStore()
    fake = FakeAgent()
    svc = SchedulerService(agent_runner=fake, store=store)

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


async def test_service_trigger_now_runs_the_task_through_the_agent() -> None:
    store = ScheduledTaskStore()
    store.create_task(_make_task(id="t1"))
    fake = FakeAgent()
    svc = SchedulerService(agent_runner=fake, store=store)

    result = await svc.trigger_now("t1")
    assert result["status"] == "success"
    assert fake.calls == [("Hello agent", "t1")]


# --------------------------------------------------------------------------- #
# SchedulerService — real Scheduler integration (end-to-end fire)
# --------------------------------------------------------------------------- #


async def test_service_real_scheduler_fires_prompt_on_next_tick() -> None:
    """With a real Scheduler, a due job fires its prompt through the runner."""
    store = ScheduledTaskStore()
    store.create_task(_make_task(id="t1", preset="every_minute"))
    fake = FakeAgent()

    svc = SchedulerService(agent_runner=fake, store=store)
    sched = Scheduler(svc._on_fire_dispatch, now_fn=lambda: 10_000)  # noqa: SLF001
    svc._scheduler = sched  # noqa: SLF001

    sched.add_job(
        Job(id="sched-t1", next_run_at=0, schedule="once", payload={"task_id": "t1"})
    )

    svc._runner = PromptRunner(
        store=store, agent_runner=fake, scheduler=sched
    )
    svc._started = True
    sched.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0)
            if fake.calls:
                break
        assert fake.calls == [("Hello agent", "t1")]
    finally:
        await svc.stop()
