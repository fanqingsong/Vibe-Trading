"""Regression tests for the fetch-level fallback in ``fetch_market_data``.

The original code only fell back to alternative data sources when a loader
failed to *construct* or reported ``is_available() == False``. A loader that
constructed fine but returned empty at ``fetch()`` time (e.g. a Tushare token
present but lacking ``daily`` permissions, a transient API error, or a symbol
the primary source has no data for) silently produced an ``_unresolved`` result
even though a working fallback source existed.

These tests pin the new behaviour: when the primary loader returns empty for
some codes, the market's fallback chain is walked until the codes resolve or
the chain is exhausted.
"""

from __future__ import annotations

import json

import pandas as pd

from src.market_data import fetch_market_data_json


def _df():
    df = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.to_datetime(["2026-05-01"]),
    )
    df.index.name = "trade_date"
    return df


# ---------------------------------------------------------------------------
# Mock loaders. The "primary" returns empty (simulating a permission-denied /
# no-data fetch); the "fallback" succeeds.
# ---------------------------------------------------------------------------


class _EmptyLoader:
    """Simulates a loader whose fetch() returns no data (e.g. permission error)."""

    name = "primary"
    markets = {"a_share"}
    requires_auth = True

    def is_available(self) -> bool:
        return True

    def fetch(self, codes, start, end, *, interval="1D", fields=None):
        return {}


class _GoodFallbackLoader:
    """The fallback source that actually has the data."""

    name = "fallback"
    markets = {"a_share"}
    requires_auth = False

    def is_available(self) -> bool:
        return True

    def fetch(self, codes, start, end, *, interval="1D", fields=None):
        return {c: _df() for c in codes}


def _call(codes, source="tushare"):
    return json.loads(
        fetch_market_data_json(
            codes=codes,
            start_date="2026-05-01",
            end_date="2026-05-02",
            source=source,
        )
    )


def test_fetch_empty_triggers_fallback(monkeypatch):
    """Primary loader returns empty -> fallback chain is walked -> data resolved."""
    from backtest.loaders import registry as reg_mod
    import src.market_data as md

    # Inject a fake registry so the fallback walk finds _GoodFallbackLoader.
    monkeypatch.setattr(reg_mod, "LOADER_REGISTRY", {"primary": _EmptyLoader, "fallback": _GoodFallbackLoader})
    monkeypatch.setattr(reg_mod, "FALLBACK_CHAINS", {"a_share": ["primary", "fallback"]})

    # loader_resolver returns the primary class (simulating get_loader_cls_with_fallback
    # which would return the primary since it constructs + is_available).
    out = _call(["600406.SH"], source="primary")
    assert "600406.SH" in out
    assert "_unresolved" not in out


def test_all_sources_empty_lands_in_unresolved(monkeypatch):
    """When every source in the chain returns empty, codes end up unresolved."""
    from backtest.loaders import registry as reg_mod

    monkeypatch.setattr(reg_mod, "LOADER_REGISTRY", {"primary": _EmptyLoader})
    monkeypatch.setattr(reg_mod, "FALLBACK_CHAINS", {"a_share": ["primary"]})

    out = _call(["600406.SH"], source="primary")
    assert out.get("_unresolved") == ["600406.SH"]


def test_no_infinite_loop_when_primary_repeats_in_chain(monkeypatch):
    """The fallback walk must skip a source it already tried (no retry loop)."""
    from backtest.loaders import registry as reg_mod

    call_count = {"n": 0}

    class _CountingEmptyLoader(_EmptyLoader):
        def fetch(self, codes, start, end, *, interval="1D", fields=None):
            call_count["n"] += 1
            return {}

    monkeypatch.setattr(reg_mod, "LOADER_REGISTRY", {"primary": _CountingEmptyLoader, "fallback": _EmptyLoader})
    # primary appears twice in the chain; each must be tried at most once.
    monkeypatch.setattr(reg_mod, "FALLBACK_CHAINS", {"a_share": ["primary", "fallback", "primary"]})

    out = _call(["600406.SH"], source="primary")
    assert out.get("_unresolved") == ["600406.SH"]
    # primary fetched once (initial) + never retried despite appearing twice in chain.
    assert call_count["n"] == 1


def test_partial_resolve_then_fallback_for_rest(monkeypatch):
    """Primary resolves some codes, fallback resolves the remainder."""
    from backtest.loaders import registry as reg_mod

    class _PartialPrimary(_EmptyLoader):
        name = "primary"

        def fetch(self, codes, start, end, *, interval="1D", fields=None):
            return {c: _df() for c in codes if c.startswith("000")}

    monkeypatch.setattr(reg_mod, "LOADER_REGISTRY", {"primary": _PartialPrimary, "fallback": _GoodFallbackLoader})
    monkeypatch.setattr(reg_mod, "FALLBACK_CHAINS", {"a_share": ["primary", "fallback"]})

    out = _call(["000001.SZ", "600406.SH"], source="primary")
    assert "000001.SZ" in out
    assert "600406.SH" in out
    assert "_unresolved" not in out
