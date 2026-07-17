"""Regression: slow /dividends must not starve the FastAPI event loop.

Symptom that shipped: opening the High Dividend page left ``screen_high_dividend``
blocking inside an ``async def`` handler. Concurrent ``/auth/login`` and
``/auth/status`` then hung forever ("login has no response").
"""

from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_auth_status_responds_while_dividends_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung dividend screen must not prevent /auth/status from answering."""
    import api_server

    entered = threading.Event()
    release = threading.Event()

    def _blocking_screen(**_kwargs):
        entered.set()
        assert release.wait(timeout=5), "test timed out waiting for release"
        return {
            "universe": "csi300",
            "market": "a_share",
            "trade_date": "20200102",
            "min_yield": 3.0,
            "max_yield": None,
            "min_market_cap": None,
            "max_pe": None,
            "market_cap_unit": "CNY_yi",
            "universe_size": 0,
            "matched": 0,
            "count": 0,
            "results": [],
            "source": "test",
        }

    # Stub the module so the endpoint's lazy import never pulls pandas/tushare.
    stub = types.ModuleType("backtest.dividend_screen")
    stub.screen_high_dividend = _blocking_screen  # type: ignore[attr-defined]
    if "backtest" not in sys.modules:
        pkg = types.ModuleType("backtest")
        pkg.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "backtest", pkg)
    monkeypatch.setitem(sys.modules, "backtest.dividend_screen", stub)

    transport = ASGITransport(app=api_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        dividends_task = asyncio.create_task(
            client.get("/dividends", params={"universe": "csi300", "top": 3})
        )
        assert await asyncio.to_thread(entered.wait, 2), (
            "dividend handler never entered the sync screener"
        )

        status_resp = await asyncio.wait_for(client.get("/auth/status"), timeout=2.0)
        assert status_resp.status_code == 200
        assert "enabled" in status_resp.json()

        release.set()
        dividends_resp = await asyncio.wait_for(dividends_task, timeout=2.0)
        assert dividends_resp.status_code == 200
        assert dividends_resp.json()["count"] == 0
