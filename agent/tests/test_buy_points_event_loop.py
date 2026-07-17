"""Regression: slow /buy-points must not starve the FastAPI event loop."""

from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest
from httpx import ASGITransport, AsyncClient


def test_auth_status_responds_while_buy_points_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung buy-point screen must not prevent /auth/status from answering."""
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
            "prior_high_lookback": 60,
            "prior_high_exclude": 5,
            "min_pullback_days": 3,
            "max_pullback_days": 15,
            "hold_tolerance": 0.02,
            "signal_freshness": 5,
            "require_volume": True,
            "volume_mult": 1.2,
            "universe_size": 0,
            "fetched": 0,
            "matched": 0,
            "count": 0,
            "results": [],
            "source": "test",
        }

    stub = types.ModuleType("backtest.buy_point_screen")
    stub.screen_right_side_buy = _blocking_screen  # type: ignore[attr-defined]
    if "backtest" not in sys.modules:
        pkg = types.ModuleType("backtest")
        pkg.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "backtest", pkg)
    monkeypatch.setitem(sys.modules, "backtest.buy_point_screen", stub)

    async def _run() -> None:
        transport = ASGITransport(app=api_server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            task = asyncio.create_task(
                client.get("/buy-points", params={"universe": "csi300", "top": 3})
            )
            assert await asyncio.to_thread(entered.wait, 2), (
                "buy-points handler never entered the sync screener"
            )

            status_resp = await asyncio.wait_for(client.get("/auth/status"), timeout=2.0)
            assert status_resp.status_code == 200
            assert "enabled" in status_resp.json()

            release.set()
            buy_resp = await asyncio.wait_for(task, timeout=2.0)
            assert buy_resp.status_code == 200
            assert buy_resp.json()["count"] == 0

    asyncio.run(_run())
