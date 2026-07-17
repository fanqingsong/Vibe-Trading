"""Tests for backtest/buy_point_screen.py signal detection."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from backtest.buy_point_screen import detect_right_side_buy, screen_right_side_buy


def _make_bars(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    start: str = "2024-01-02",
) -> pd.DataFrame:
    n = len(closes)
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    dates = [start_dt + timedelta(days=i) for i in range(n)]
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame(
        {
            "trade_date": dates,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def _pattern_frame(
    *,
    prior_level: float = 100.0,
    breakout_close: float = 105.0,
    pullback_low: float = 99.0,
    reclaim_close: float = 101.0,
    lookback: int = 60,
    exclude: int = 5,
    pullback_days: int = 5,
    trailing: int = 2,
    breakout_volume: float = 2_000_000.0,
    base_volume: float = 1_000_000.0,
) -> pd.DataFrame:
    """Build a synthetic series that forms one right-side buy at the end."""
    # Flat base for prior-high window (and exclude window), then breakout,
    # pullback, reclaim, optional trailing bars after signal.
    base_len = lookback + exclude
    closes = [prior_level * 0.9] * base_len
    highs = [prior_level] * base_len  # prior high touches `prior_level`
    lows = [prior_level * 0.85] * base_len
    volumes = [base_volume] * base_len

    # Breakout bar
    closes.append(breakout_close)
    highs.append(breakout_close * 1.01)
    lows.append(prior_level * 0.95)
    volumes.append(breakout_volume)

    # Pullback bars (days between breakout and reclaim)
    for i in range(pullback_days - 1):
        # Stay below prior high on close until the reclaim day.
        c = prior_level * 0.99
        closes.append(c)
        highs.append(prior_level * 0.995)
        # Deepest low on one bar; other bars stay shallower.
        low = pullback_low if i == max(0, pullback_days // 2 - 1) else prior_level * 0.995
        lows.append(low)
        volumes.append(base_volume)

    # Reclaim / signal bar
    closes.append(reclaim_close)
    highs.append(reclaim_close * 1.01)
    lows.append(prior_level * 0.99)
    volumes.append(base_volume)

    for _ in range(trailing):
        closes.append(reclaim_close * 1.01)
        highs.append(reclaim_close * 1.02)
        lows.append(reclaim_close * 0.99)
        volumes.append(base_volume)

    return _make_bars(closes, highs=highs, lows=lows, volumes=volumes)


class TestDetectRightSideBuy:
    def test_detects_breakout_pullback_reclaim(self):
        df = _pattern_frame(trailing=0)
        signal = detect_right_side_buy(
            df,
            prior_high_lookback=60,
            prior_high_exclude=5,
            min_pullback_days=3,
            max_pullback_days=15,
            hold_tolerance=0.02,
            signal_freshness=5,
            require_volume=True,
            volume_mult=1.2,
        )
        assert signal is not None
        assert signal["prior_high"] == pytest.approx(100.0)
        assert signal["pullback_low"] == pytest.approx(99.0)
        assert signal["breakout_pct"] == pytest.approx(5.0)
        assert signal["volume_ratio"] == pytest.approx(2.0)
        assert signal["days_since_signal"] == 0

    def test_rejects_when_pullback_breaks_floor(self):
        df = _pattern_frame(pullback_low=96.0)  # >2% below 100
        signal = detect_right_side_buy(df, hold_tolerance=0.02, require_volume=True)
        assert signal is None

    def test_rejects_without_volume_when_required(self):
        df = _pattern_frame(breakout_volume=1_000_000.0)  # no expansion
        assert detect_right_side_buy(df, require_volume=True, volume_mult=1.2) is None
        assert detect_right_side_buy(df, require_volume=False) is not None

    def test_stale_signal_outside_freshness(self):
        # Signal then many trailing bars → reclaim no longer in freshness window.
        df = _pattern_frame(trailing=10)
        signal = detect_right_side_buy(df, signal_freshness=5, require_volume=True)
        assert signal is None

    def test_insufficient_history(self):
        df = _make_bars([100.0] * 20)
        assert detect_right_side_buy(df) is None


class TestScreenValidation:
    def test_rejects_mixed_custom_universe(self, monkeypatch: pytest.MonkeyPatch):
        from backtest import buy_point_screen as mod

        monkeypatch.setattr(
            mod,
            "resolve_universe_codes",
            lambda universe, codes=None: ["600036.SH", "AAPL"],
        )
        with pytest.raises(ValueError, match="cannot mix"):
            screen_right_side_buy(universe="custom", codes=["600036.SH", "AAPL"])

    def test_screen_ranks_by_freshness_then_breakout_pct(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from backtest import buy_point_screen as mod

        stronger = _pattern_frame(breakout_close=110.0, trailing=0)
        weaker = _pattern_frame(breakout_close=103.0, trailing=0)

        monkeypatch.setattr(
            mod,
            "resolve_universe_codes",
            lambda universe, codes=None: ["AAA", "BBB"],
        )
        monkeypatch.setattr(
            mod,
            "_fetch_ohlcv",
            lambda codes, market: (
                {"AAA": weaker, "BBB": stronger},
                "test",
            ),
        )

        out = screen_right_side_buy(
            universe="custom",
            codes=["AAA", "BBB"],
            require_volume=True,
            top=10,
        )
        assert out["count"] == 2
        assert out["results"][0]["code"] == "BBB"
        assert out["results"][0]["breakout_pct"] > out["results"][1]["breakout_pct"]

    def test_a_share_small_universe_skips_bulk(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Tiny custom lists should not wait on by-date bulk pulls."""
        from backtest import buy_point_screen as mod
        import backtest.loaders.registry as reg

        calls: list[str] = []
        frame = _pattern_frame(trailing=0)

        def _bulk(*_a, **_k):
            calls.append("tushare_bulk")
            return {}, False

        monkeypatch.setattr(mod, "_fetch_a_share_tushare_bulk", _bulk)

        class _FakeLoader:
            def __init__(self, name: str):
                self.name = name

            def is_available(self) -> bool:
                return self.name == "akshare"

            def fetch(self, codes, **_kwargs):
                calls.append(self.name)
                return {codes[0]: frame}

        registry = {
            "mootdx": lambda: _FakeLoader("mootdx"),
            "akshare": lambda: _FakeLoader("akshare"),
            "tushare": lambda: _FakeLoader("tushare"),
        }
        monkeypatch.setattr(reg, "_ensure_registered", lambda: None)
        monkeypatch.setattr(reg, "LOADER_REGISTRY", registry)

        out, source = mod._fetch_ohlcv(["600036.SH"], market="a_share")
        assert source == "akshare"
        assert "600036.SH" in out
        assert "tushare_bulk" not in calls

    def test_a_share_large_universe_tries_bulk_first(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from backtest import buy_point_screen as mod
        import backtest.loaders.registry as reg

        frame = _pattern_frame(trailing=0)
        codes = [f"{i:06d}.SH" for i in range(25)]

        monkeypatch.setattr(
            mod,
            "_fetch_a_share_tushare_bulk",
            lambda req, **kwargs: ({req[0]: frame}, False),
        )
        monkeypatch.setattr(reg, "_ensure_registered", lambda: None)
        monkeypatch.setattr(reg, "LOADER_REGISTRY", {})

        out, source = mod._fetch_ohlcv(codes, market="a_share")
        assert source == "tushare_bulk"
        assert codes[0] in out
