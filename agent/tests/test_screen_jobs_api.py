"""POST/GET /screen/{kind}/jobs — refresh-survivable screen job endpoints.

Testing note: Starlette's TestClient portal joins spawned asyncio tasks, so a
POST does not return to the test thread until its background worker finishes
(uvicorn in production sends the response immediately — same pattern as the
alpha bench jobs). HTTP tests therefore use instant stub runners or pre-seeded
job-store entries; the queued→running transition is covered by invoking the
worker function directly with a gated stub.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import screen_routes


@pytest.fixture
def client(db_session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_baostock_supported", lambda: False)
    monkeypatch.setattr(api_server, "_baostock_installed", lambda: False)
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    # Fresh job store per test; the module store is process-global.
    monkeypatch.setattr(screen_routes, "SCREEN_JOBS", {})
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _instant_screen(
    monkeypatch: pytest.MonkeyPatch, module: str, func_name: str, calls: list | None = None
) -> None:
    """Stub a screen runner that returns immediately (records its kwargs)."""

    def _fake(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return {"status": "ok", "results": [], "called_with": kwargs}

    monkeypatch.setattr(f"{module}.{func_name}", _fake)


def _seed_job(
    kind: str,
    params: dict,
    status: str,
    job_id: str = "seedjob000000000000000000000000",
    **over,
) -> dict:
    """Insert a synthetic job entry as if a POST had created/finished it."""
    now = time.time()
    job = {
        "job_id": job_id,
        "kind": kind,
        "status": status,
        "params": params,
        "_params_key": screen_routes._params_key(
            kind,
            screen_routes._PARAM_MODELS[kind](**params).model_dump(exclude_none=True),
        ),
        "_created_at": now,
        "created_at": "2026-08-18T00:00:00+00:00",
        "result": None,
        "error": None,
    }
    if status in ("done", "error"):
        job["_finished_at"] = now
    job.update(over)
    screen_routes.SCREEN_JOBS[job_id] = job
    return job


# ---------------------------------------------------------------------------
# Validation / lookup failures
# ---------------------------------------------------------------------------


def test_unknown_kind_returns_404(client: TestClient) -> None:
    assert client.post("/screen/nope/jobs", json={"params": {}}).status_code == 404
    assert client.get("/screen/nope/jobs/whatever").status_code == 404


def test_invalid_params_return_422(client: TestClient) -> None:
    response = client.post(
        "/screen/chanlun/jobs", json={"params": {"buy_type": "buy9"}}
    )
    assert response.status_code == 422
    assert "buy_type" in response.text
    assert "buy_type must be one of" in response.text


def test_unknown_job_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/screen/dividends/jobs/doesnotexist").status_code == 404
    # Job ids are namespaced per kind — a dividends job is not visible under
    # buy-points. (Runner stubbed so no real screen fires.)
    _instant_screen(monkeypatch, "backtest.dividend_screen", "screen_high_dividend")
    started = client.post("/screen/dividends/jobs", json={"params": {}}).json()
    assert (
        client.get(f"/screen/buy-points/jobs/{started['job_id']}").status_code == 404
    )


# ---------------------------------------------------------------------------
# Lifecycle over HTTP (instant stubs → deterministic under TestClient)
# ---------------------------------------------------------------------------


def test_job_lifecycle_completes_with_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _instant_screen(monkeypatch, "backtest.chanlun_screen", "screen_chanlun_buy")

    started = client.post(
        "/screen/chanlun/jobs", json={"params": {"buy_type": "buy3", "top": 10}}
    )
    assert started.status_code == 202
    body = started.json()
    assert body["reused"] is None

    # Under TestClient the worker finishes before the POST returns, so the
    # first poll already observes the terminal state (production sees
    # queued/running here — covered by the direct worker test below).
    status = client.get(f"/screen/chanlun/jobs/{body['job_id']}")
    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "done"
    assert payload["result"]["called_with"]["buy_type"] == "buy3"
    assert payload["elapsed_seconds"] >= 0


def test_vendor_error_surfaces_as_job_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs):
        raise RuntimeError("tushare rate limited")

    monkeypatch.setattr("backtest.dividend_screen.screen_high_dividend", _boom)

    started = client.post("/screen/dividends/jobs", json={"params": {}}).json()
    payload = client.get(f"/screen/dividends/jobs/{started['job_id']}").json()
    assert payload["status"] == "error"
    assert "rate limited" in payload["error"]
    assert payload["result"] is None


# ---------------------------------------------------------------------------
# Worker state machine (direct call, gated stub → observes "running")
# ---------------------------------------------------------------------------


def test_worker_transitions_running_then_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs: dict = {}
    monkeypatch.setattr(screen_routes, "SCREEN_JOBS", jobs)

    gate = threading.Event()

    def _fake(**_kwargs):
        gate.wait(timeout=10)
        return {"status": "ok", "results": ["row1"]}

    monkeypatch.setattr("backtest.dividend_screen.screen_high_dividend", _fake)

    _seed_job("dividends", {"universe": "csi300"}, "queued", job_id="w1")
    worker = threading.Thread(
        target=screen_routes._run_screen_blocking,
        args=("w1", "dividends", {"universe": "csi300"}),
    )
    worker.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and jobs["w1"]["status"] != "running":
            time.sleep(0.05)
        assert jobs["w1"]["status"] == "running"

        snapshot = screen_routes._job_snapshot(jobs["w1"])
        assert snapshot["status"] == "running"
        assert snapshot["elapsed_seconds"] >= 0
        assert snapshot["result"] is None

        gate.set()
    finally:
        gate.set()  # never leave the worker blocked on a failed assert
    worker.join(timeout=10)

    assert jobs["w1"]["status"] == "done"
    assert screen_routes._job_snapshot(jobs["w1"])["result"] == {
        "status": "ok",
        "results": ["row1"],
    }


def test_worker_sanitises_unexpected_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs: dict = {}
    monkeypatch.setattr(screen_routes, "SCREEN_JOBS", jobs)

    def _kaboom(**_kwargs):
        raise KeyError("/etc/secret/path")

    monkeypatch.setattr("backtest.dividend_screen.screen_high_dividend", _kaboom)

    _seed_job("dividends", {"universe": "csi300"}, "queued", job_id="w2")
    screen_routes._run_screen_blocking("w2", "dividends", {"universe": "csi300"})

    assert jobs["w2"]["status"] == "error"
    # Curated vendor errors surface verbatim; unexpected ones must not leak.
    assert "secret" not in (jobs["w2"]["error"] or "")
    assert "KeyError" in jobs["w2"]["error"]


# ---------------------------------------------------------------------------
# Dedupe / reuse semantics
# ---------------------------------------------------------------------------


def test_identical_running_job_is_reused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    _instant_screen(
        monkeypatch, "backtest.dividend_screen", "screen_high_dividend", calls
    )
    params = {"min_yield": 4.5, "top": 20}
    seeded = _seed_job("dividends", params, "running")

    second = client.post("/screen/dividends/jobs", json={"params": params}).json()
    assert second["job_id"] == seeded["job_id"]
    assert second["reused"] == "running"
    # No new worker ran — the running job was simply re-attached to.
    assert calls == []


def test_auto_load_reuses_recent_done_job_but_fresh_recomputes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    _instant_screen(
        monkeypatch, "backtest.buy_point_screen", "screen_right_side_buy", calls
    )
    params = {"prior_high_lookback": 60, "top": 50}
    seeded = _seed_job(
        "buy-points", params, "done", result={"status": "ok", "results": ["cached"]}
    )

    # fresh=false (page auto-load after refresh) → reuse the done job.
    reused = client.post("/screen/buy-points/jobs", json={"params": params}).json()
    assert reused["job_id"] == seeded["job_id"]
    assert reused["reused"] == "done"
    assert calls == []
    status = client.get(f"/screen/buy-points/jobs/{seeded['job_id']}").json()
    assert status["result"] == {"status": "ok", "results": ["cached"]}

    # fresh=true (explicit Screen click) → recompute with a new job.
    fresh = client.post(
        "/screen/buy-points/jobs", json={"params": params, "fresh": True}
    ).json()
    assert fresh["job_id"] != seeded["job_id"]
    assert fresh["reused"] is None
    payload = client.get(f"/screen/buy-points/jobs/{fresh['job_id']}").json()
    assert payload["status"] == "done"
    assert len(calls) == 1


def test_different_params_start_new_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    _instant_screen(
        monkeypatch, "backtest.dividend_screen", "screen_high_dividend", calls
    )
    _seed_job("dividends", {"min_yield": 3.0, "top": 50}, "running")

    other = client.post(
        "/screen/dividends/jobs", json={"params": {"min_yield": 6.0, "top": 50}}
    ).json()
    assert other["reused"] is None
    assert other["job_id"] != "seedjob000000000000000000000000"
    assert len(calls) == 1


def test_stale_done_job_is_pruned_by_ttl(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    _instant_screen(
        monkeypatch, "backtest.dividend_screen", "screen_high_dividend", calls
    )
    stale_id = "stalejob00000000000000000000000"
    stale = _seed_job("dividends", {"min_yield": 3.0}, "done", job_id=stale_id)
    stale["_finished_at"] = time.time() - screen_routes._JOB_TTL_SECONDS - 1

    aged = client.post(
        "/screen/dividends/jobs",
        json={"params": {"min_yield": 9.9}},
    ).json()
    assert aged["job_id"] != stale_id
    # The pruned job is gone; a client polling it gets a 404 and restarts.
    assert client.get(f"/screen/dividends/jobs/{stale_id}").status_code == 404
