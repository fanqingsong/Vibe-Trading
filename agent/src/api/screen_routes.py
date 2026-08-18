"""Persistent (refresh-survivable) job wrapper for the three Web-UI screeners.

Mounted by ``agent/api_server.py`` via ``register_screen_routes(app)``. The
synchronous GET endpoints (``/dividends``, ``/buy-points``, ``/chanlun``) block
for up to ~2 minutes on a cold run, so a browser refresh kills the frontend's
view of the in-flight screen and the page auto-starts a duplicate run. These
routes move the same screen functions behind a background job API so the UI can
re-attach to the running job after a refresh:

- ``POST /screen/{kind}/jobs``         — start (or re-attach to) a screen job
- ``GET  /screen/{kind}/jobs/{job_id}`` — poll status; ``result`` when done

``kind`` is one of ``dividends`` | ``buy-points`` | ``chanlun``. Job state lives
in the module-level ``SCREEN_JOBS`` dict guarded by ``_JOBS_LOCK`` (same pattern
as ``src/api/alpha_routes.py``): in-memory only, so a process restart wipes job
state — the frontend treats the resulting 404 as "start a new screen".

Dedupe: jobs are keyed by ``kind`` + a canonical params key. A POST with the
same params while one is running returns the running job (no duplicate vendor
pulls / Tushare rate-limit waste). With ``fresh=false`` (page auto-load /
refresh) a recently finished job with the same params is returned as-is, so a
refresh right after completion shows the table instantly; ``fresh=true``
(explicit Screen click) recomputes unless an identical run is in flight.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Job store (in-memory, process-local)
# ---------------------------------------------------------------------------

SCREEN_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

# Live background screen tasks. Holding strong refs prevents the asyncio GC
# from cancelling fire-and-forget tasks; ``add`` on create, ``discard`` on done.
_RUNNING_TASKS: set[asyncio.Task[Any]] = set()

# Finished (done/error) jobs are pruned after this TTL — long enough that a
# page refresh right after a screen finishes still shows the table, short
# enough that stale results don't pile up (the row payloads are large).
_JOB_TTL_SECONDS = 10 * 60

# A screen pulls the full universe's daily bars (Tushare/AKShare); more than a
# couple in flight just burns rate limit. 429 when the cap is hit.
MAX_CONCURRENT_SCREENS = 2
_SCREEN_SEMAPHORE: asyncio.Semaphore | None = None
_SCREEN_SEMAPHORE_LOCK = threading.Lock()

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_KINDS = ("dividends", "buy-points", "chanlun")
_VALID_UNIVERSES = ("csi300", "sp500", "custom")
_VALID_BUY_TYPES = ("buy1", "buy2", "buy3")


def _get_screen_semaphore() -> asyncio.Semaphore:
    """Return the process-wide screen semaphore, building it on first call."""
    global _SCREEN_SEMAPHORE
    with _SCREEN_SEMAPHORE_LOCK:
        if _SCREEN_SEMAPHORE is None:
            _SCREEN_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SCREENS)
        return _SCREEN_SEMAPHORE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _prune_old_jobs() -> None:
    """Drop done/errored screen jobs older than ``_JOB_TTL_SECONDS``."""
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _JOBS_LOCK:
        stale = [
            jid for jid, job in SCREEN_JOBS.items()
            if job.get("status") in ("done", "error")
            and job.get("_finished_at", 0) < cutoff
        ]
        for jid in stale:
            SCREEN_JOBS.pop(jid, None)


# ---------------------------------------------------------------------------
# Request schemas — constraints mirror the synchronous GET endpoints
# ---------------------------------------------------------------------------


def _parse_codes(codes: list[str] | None) -> list[str] | None:
    if not codes:
        return None
    out = [c.strip() for c in codes if str(c).strip()]
    if len(out) > 500:
        raise ValueError("Maximum 500 codes per request")
    return out or None


class _CodesModel(BaseModel):
    """Shared universe / codes fields (same validation as the GET endpoints)."""

    universe: str = Field("csi300", min_length=1, max_length=32)
    codes: list[str] | None = Field(
        None, description="Ticker list (required when universe=custom)"
    )

    @field_validator("universe")
    @classmethod
    def _universe_known(cls, v: str) -> str:
        key = v.strip().lower()
        if key not in _VALID_UNIVERSES:
            raise ValueError(
                f"universe must be one of: {', '.join(_VALID_UNIVERSES)}"
            )
        return key

    @field_validator("codes")
    @classmethod
    def _codes_bounded(cls, v: list[str] | None) -> list[str] | None:
        try:
            return _parse_codes(v)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class DividendScreenParams(_CodesModel):
    """POST /screen/dividends/jobs body — mirrors GET /dividends."""

    min_yield: float = Field(3.0, ge=0, le=100)
    max_yield: float | None = Field(None, ge=0, le=100)
    min_market_cap: float | None = Field(None, ge=0)
    max_pe: float | None = Field(None, ge=0)
    top: int = Field(50, ge=1, le=500)
    trade_date: str | None = Field(None, max_length=16)


class BuyPointScreenParams(_CodesModel):
    """POST /screen/buy-points/jobs body — mirrors GET /buy-points."""

    prior_high_lookback: int = Field(60, ge=10, le=250)
    prior_high_exclude: int = Field(5, ge=0, le=30)
    min_pullback_days: int = Field(3, ge=1, le=30)
    max_pullback_days: int = Field(15, ge=1, le=60)
    hold_tolerance: float = Field(0.02, ge=0, le=0.2)
    signal_freshness: int = Field(10, ge=1, le=30)
    require_volume: bool = True
    volume_mult: float = Field(1.2, ge=0.5, le=5.0)
    top: int = Field(50, ge=1, le=500)


class ChanlunScreenParams(_CodesModel):
    """POST /screen/chanlun/jobs body — mirrors GET /chanlun."""

    buy_type: str = Field("buy3", min_length=4, max_length=8)
    signal_freshness: int = Field(10, ge=1, le=60)
    ma_period: int = Field(34, ge=2, le=120)
    top: int = Field(50, ge=1, le=500)

    @field_validator("buy_type")
    @classmethod
    def _buy_type_known(cls, v: str) -> str:
        key = v.strip().lower()
        if key not in _VALID_BUY_TYPES:
            raise ValueError(f"buy_type must be one of: {', '.join(_VALID_BUY_TYPES)}")
        return key


class StartScreenJobRequest(BaseModel):
    """Envelope for POST /screen/{kind}/jobs.

    ``params`` is polymorphic across kinds, so it is modelled as a raw dict
    here and parsed against the per-kind schema inside the handler.
    """

    params: dict[str, Any] = Field(default_factory=dict)
    fresh: bool = Field(
        False,
        description=(
            "true = explicit Screen click (recompute unless an identical run "
            "is in flight); false = page auto-load (reuse a recent result)"
        ),
    )


_PARAM_MODELS: dict[str, type[BaseModel]] = {
    "dividends": DividendScreenParams,
    "buy-points": BuyPointScreenParams,
    "chanlun": ChanlunScreenParams,
}


# ---------------------------------------------------------------------------
# Screen workers (run in a thread via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _run_screen_blocking(
    job_id: str,
    kind: str,
    params: dict[str, Any],
) -> None:
    """Synchronous screen worker — calls the same functions as the GETs."""
    import backtest.dividend_screen as dividend_screen
    import backtest.buy_point_screen as buy_point_screen
    import backtest.chanlun_screen as chanlun_screen

    runners: dict[str, Callable[[], dict[str, Any]]] = {
        "dividends": lambda: dividend_screen.screen_high_dividend(**params),
        "buy-points": lambda: buy_point_screen.screen_right_side_buy(**params),
        "chanlun": lambda: chanlun_screen.screen_chanlun_buy(**params),
    }

    with _JOBS_LOCK:
        job = SCREEN_JOBS.get(job_id)
        if job is not None:
            job["status"] = "running"

    try:
        result = runners[kind]()
    except (ValueError, RuntimeError) as exc:
        # Curated vendor/data errors (bad universe, rate limit, ...) — safe to
        # surface, exactly like the synchronous endpoints do.
        _finish_job(job_id, "error", error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — worker must never crash the loop
        logger.exception("screen job worker crashed (job=%s kind=%s)", job_id, kind)
        _finish_job(job_id, "error", error=f"{type(exc).__name__}: screen failed")
        return

    _finish_job(job_id, "done", result=result)


def _finish_job(
    job_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with _JOBS_LOCK:
        job = SCREEN_JOBS.get(job_id)
        if job is None:
            return
        job["status"] = status
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        job["_finished_at"] = time.time()


def _params_key(kind: str, params: dict[str, Any]) -> str:
    """Canonical dedupe key for a screen job (kind + sorted-JSON params)."""
    return kind + ":" + json.dumps(params, sort_keys=True, ensure_ascii=False)


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    """Wire shape for GET /screen/{kind}/jobs/{job_id}."""
    status = job["status"]
    end = job.get("_finished_at")
    elapsed = (end if end is not None else time.time()) - job["_created_at"]
    out: dict[str, Any] = {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": status,
        "elapsed_seconds": round(max(0.0, elapsed), 1),
        "created_at": job["created_at"],
        "result": None,
        "error": None,
    }
    if status == "done":
        out["result"] = job.get("result")
    elif status == "error":
        out["error"] = job.get("error")
    return out


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_screen_routes(
    app: FastAPI,
    require_auth: Callable[..., Awaitable[Any] | Any] | None = None,
) -> None:
    """Mount the screen-job routes onto ``app``.

    Args:
        app: The host FastAPI app.
        require_auth: Optional auth dependency for both endpoints. When
            omitted the routes stay open, matching the synchronous
            ``/dividends`` / ``/buy-points`` / ``/chanlun`` GET endpoints.
    """
    guards = [Depends(require_auth)] if require_auth is not None else []

    # -----------------------------------------------------------------------
    # POST /screen/{kind}/jobs
    # -----------------------------------------------------------------------

    @app.post("/screen/{kind}/jobs", status_code=202, dependencies=guards)
    async def start_screen_job(kind: str, payload: StartScreenJobRequest) -> dict[str, Any]:
        """Start (or re-attach to) a background screen job."""
        if kind not in _KINDS:
            raise HTTPException(
                status_code=404,
                detail=f"unknown screen kind {kind!r}; expected one of: {', '.join(_KINDS)}",
            )

        # Params are validated against the per-kind schema here (not via a
        # typed body field) so pydantic's error must be surfaced manually.
        # ``ctx`` is dropped — it carries the raw ValueError object.
        try:
            model = _PARAM_MODELS[kind](**payload.params)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=[
                    {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
                    for e in exc.errors(include_url=False)
                ],
            ) from exc
        params = model.model_dump(exclude_none=True)

        key = _params_key(kind, params)
        _prune_old_jobs()

        # Re-attach: identical run in flight → same job regardless of fresh.
        # fresh=false (page auto-load) also reuses a recent finished job so a
        # refresh right after completion doesn't recompute.
        with _JOBS_LOCK:
            for job in SCREEN_JOBS.values():
                if job.get("_params_key") != key:
                    continue
                if job["status"] in ("queued", "running"):
                    return {"job_id": job["job_id"], "status": job["status"], "reused": "running"}
                if (
                    not payload.fresh
                    and job["status"] == "done"
                ):
                    return {"job_id": job["job_id"], "status": "done", "reused": "done"}

        sem = _get_screen_semaphore()
        # ``locked()`` returns True iff the counter is 0 (see alpha_routes).
        if sem.locked() or getattr(sem, "_value", MAX_CONCURRENT_SCREENS) <= 0:
            raise HTTPException(
                status_code=429,
                detail="too many running screens; wait for one to finish",
            )

        job_id = uuid.uuid4().hex
        with _JOBS_LOCK:
            SCREEN_JOBS[job_id] = {
                "job_id": job_id,
                "kind": kind,
                "status": "queued",
                "params": params,
                "_params_key": key,
                "_created_at": time.time(),
                "created_at": _now_iso(),
                "result": None,
                "error": None,
            }

        async def _runner() -> None:
            async with sem:
                try:
                    await asyncio.to_thread(_run_screen_blocking, job_id, kind, params)
                except Exception:  # noqa: BLE001 — never escape the loop
                    logger.exception("screen runner outer task crashed (job=%s)", job_id)
                    _finish_job(job_id, "error", error="internal error; see server logs")

        task = asyncio.create_task(_runner())
        _RUNNING_TASKS.add(task)
        task.add_done_callback(_RUNNING_TASKS.discard)
        return {"job_id": job_id, "status": "queued", "reused": None}

    # -----------------------------------------------------------------------
    # GET /screen/{kind}/jobs/{job_id}
    # -----------------------------------------------------------------------

    @app.get("/screen/{kind}/jobs/{job_id}", dependencies=guards)
    async def get_screen_job(kind: str, job_id: str) -> dict[str, Any]:
        """Poll a screen job; 404 when unknown or evicted (client re-starts)."""
        if kind not in _KINDS:
            raise HTTPException(
                status_code=404,
                detail=f"unknown screen kind {kind!r}; expected one of: {', '.join(_KINDS)}",
            )
        if not _JOB_ID_RE.fullmatch(job_id or ""):
            raise HTTPException(status_code=400, detail="invalid job_id")
        with _JOBS_LOCK:
            job = SCREEN_JOBS.get(job_id)
            if job is None or job["kind"] != kind:
                raise HTTPException(status_code=404, detail=f"job {job_id} not found")
            return _job_snapshot(job)
