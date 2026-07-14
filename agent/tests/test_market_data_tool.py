from __future__ import annotations

import json

import pandas as pd

from src.market_data import fetch_market_data_json
from src.swarm.models import SwarmAgentSpec
from src.swarm.presets import list_presets, load_preset
from src.swarm.worker import build_worker_prompt
from src.tools import build_swarm_registry
from src.tools.market_data_tool import MarketDataTool


def test_get_market_data_is_repeatable():
    """Same-session re-fetches (new codes/dates) must not be tool_skipped."""
    assert MarketDataTool.repeatable is True


def test_long_range_keeps_recent_july_under_agent_tool_budget():
    """AgentLoop keeps only the first 10k chars of tool results.

    Pretty multi-month OHLCV used to truncate mid-series so the model never saw
    July and answered 'July data missing'. Compact JSON + keep-recent fitting
    must leave July bars inside that 10k window.
    """
    from src.market_data import AGENT_TOOL_RESULT_CHAR_BUDGET, fit_market_data_payload

    idx = pd.date_range("2026-01-01", "2026-07-14", freq="B")
    rows = [
        {
            "trade_date": ts.isoformat(),
            "open": 20.0,
            "high": 21.0,
            "low": 19.0,
            "close": 20.5,
            "volume": 100000.0,
        }
        for ts in idx
    ]
    payload = {"000895.SZ": rows}
    # Simulate old pretty dump blowing past the agent limit.
    pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    assert len(pretty) > 10_000
    assert "2026-07" not in pretty[:10_000]

    fitted = fit_market_data_payload(payload, max_chars=AGENT_TOOL_RESULT_CHAR_BUDGET)
    text = json.dumps(fitted, ensure_ascii=False, allow_nan=False)
    assert len(text) <= AGENT_TOOL_RESULT_CHAR_BUDGET
    assert "2026-07-14" in text
    assert fitted.get("_truncated", {}).get("policy") == "kept_most_recent_bars"


def test_market_data_json_is_strict_when_loader_returns_nan():
    idx = pd.date_range("2026-01-01", periods=1, freq="D")
    df = pd.DataFrame(
        {
            "open": [1.0],
            "high": [float("nan")],
            "low": [0.9],
            "close": [1.1],
            "volume": [100],
        },
        index=idx,
    )
    df.index.name = "trade_date"

    class _Loader:
        def fetch(self, codes, start, end, interval="1D"):
            return {"X.US": df}

    text = fetch_market_data_json(
        codes=["X.US"],
        start_date="2026-01-01",
        end_date="2026-01-02",
        source="yfinance",
        loader_resolver=lambda source: _Loader,
    )

    assert "NaN" not in text
    payload = json.loads(text)
    assert payload["X.US"][0]["high"] is None


def test_swarm_registry_can_expose_local_get_market_data_tool():
    registry = build_swarm_registry(["get_market_data"])

    assert "get_market_data" in registry.tool_names


def test_every_market_data_worker_has_get_market_data_tool():
    """Workers with OHLCV-capable skills must expose the loader-backed tool (#198)."""
    market_data_skills = {"tushare", "yfinance", "okx-market"}
    missing = []
    for summary in list_presets():
        preset = load_preset(summary["name"])
        for agent in preset.get("agents", []):
            if market_data_skills & set(agent.get("skills", [])):
                if "get_market_data" not in (agent.get("tools") or []):
                    missing.append(f"{summary['name']}:{agent['id']}")

    assert not missing, f"workers with market-data skills lack get_market_data: {missing}"


def test_worker_prompt_prioritizes_get_market_data_for_ohlcv():
    spec = SwarmAgentSpec(
        id="analyst",
        role="Analyst",
        system_prompt="Analyze prices.",
        tools=["load_skill", "get_market_data", "write_file"],
        skills=["yfinance"],
    )

    prompt = build_worker_prompt(spec, {}, "  - yfinance: market data")

    assert "Market Data Tool Policy" in prompt
    assert "call `get_market_data` before writing raw provider scripts" in prompt
