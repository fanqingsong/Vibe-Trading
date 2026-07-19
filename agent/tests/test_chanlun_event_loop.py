"""Regression: slow /chanlun must not starve the FastAPI event loop."""

from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest
from httpx import ASGITransport, AsyncClient


def test_auth_status_responds_while_chanlun_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung Chanlun screen must not prevent /auth/status from answering."""
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
            "buy_type": "buy3",
            "buy_label": "三买",
            "signal_freshness": 10,
            "ma_period": 34,
            "universe_size": 0,
            "fetched": 0,
            "matched": 0,
            "count": 0,
            "results": [],
            "source": "test",
        }

    stub = types.ModuleType("backtest.chanlun_screen")
    stub.screen_chanlun_buy = _blocking_screen  # type: ignore[attr-defined]
    if "backtest" not in sys.modules:
        pkg = types.ModuleType("backtest")
        pkg.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "backtest", pkg)
    monkeypatch.setitem(sys.modules, "backtest.chanlun_screen", stub)

    async def _run() -> None:
        transport = ASGITransport(app=api_server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            task = asyncio.create_task(
                client.get(
                    "/chanlun",
                    params={"universe": "csi300", "buy_type": "buy3", "top": 3},
                )
            )
            assert await asyncio.to_thread(entered.wait, 2), (
                "chanlun handler never entered the sync screener"
            )

            status_resp = await asyncio.wait_for(client.get("/auth/status"), timeout=2.0)
            assert status_resp.status_code == 200
            assert "enabled" in status_resp.json()

            release.set()
            chanlun_resp = await asyncio.wait_for(task, timeout=2.0)
            assert chanlun_resp.status_code == 200
            assert chanlun_resp.json()["count"] == 0

    asyncio.run(_run())
