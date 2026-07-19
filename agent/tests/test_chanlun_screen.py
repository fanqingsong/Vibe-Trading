"""Tests for backtest/chanlun_screen.py."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from backtest.chanlun_screen import (
    _BUY_LABELS,
    _match_buy,
    detect_chanlun_buy,
    screen_chanlun_buy,
)


def _make_bars(n: int = 80, start: str = "2024-01-02") -> pd.DataFrame:
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    dates = [start_dt + timedelta(days=i) for i in range(n)]
    closes = [100.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * n,
        }
    )


class TestMatchBuy:
    def test_matches_label_and_rejects_other(self):
        assert _match_buy({"k": "三买_均线新高_任意_0"}, "buy3") == (
            True,
            "三买_均线新高_任意_0",
        )
        assert _match_buy({"k": "其他_任意_任意_0"}, "buy3")[0] is False
        assert _match_buy({"k": "一买_15笔_任意_0"}, "buy1")[0] is True
        assert _match_buy({"k": "一买_15笔_任意_0"}, "buy3")[0] is False


class TestDetectChanlunBuy:
    def test_detects_onset_within_freshness(self, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("czsc")
        from backtest import chanlun_screen as mod

        df = _make_bars(80)
        # warmup = 59 for n=80; activate starting at bar id 75.
        onset_id = 75

        def fake_collect(c, *, buy_type, ma_period):
            bars = list(getattr(c, "bars_raw", []) or [])
            last_id = bars[-1].id if bars else -1
            if last_id >= onset_id:
                return {"sig": f"{_BUY_LABELS[buy_type]}_任意_任意_0"}
            return {"sig": "其他_任意_任意_0"}

        monkeypatch.setattr(mod, "_collect_signals", fake_collect)
        monkeypatch.setattr(mod, "_extract_zs_levels", lambda _c: (110.0, 100.0))

        signal = detect_chanlun_buy(
            df, buy_type="buy3", signal_freshness=10, ma_period=34
        )
        assert signal is not None
        assert signal["buy_type"] == "buy3"
        assert signal["buy_label"] == "三买"
        assert signal["zg"] == pytest.approx(110.0)
        assert signal["zd"] == pytest.approx(100.0)
        assert signal["days_since_signal"] == 80 - 1 - onset_id
        expected_date = pd.Timestamp(df["trade_date"].iloc[onset_id]).strftime(
            "%Y-%m-%d"
        )
        assert signal["signal_date"] == expected_date

    def test_stale_onset_outside_freshness(self, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("czsc")
        from backtest import chanlun_screen as mod

        df = _make_bars(80)
        onset_id = 50  # well before freshness window

        def fake_collect(c, *, buy_type, ma_period):
            bars = list(getattr(c, "bars_raw", []) or [])
            last_id = bars[-1].id if bars else -1
            if last_id >= onset_id:
                return {"sig": "三买_任意_任意_0"}
            return {"sig": "其他_任意_任意_0"}

        monkeypatch.setattr(mod, "_collect_signals", fake_collect)
        monkeypatch.setattr(mod, "_extract_zs_levels", lambda _c: (None, None))

        assert (
            detect_chanlun_buy(df, buy_type="buy3", signal_freshness=5) is None
        )

    def test_insufficient_history(self):
        pytest.importorskip("czsc")
        df = _make_bars(20)
        assert detect_chanlun_buy(df) is None

    def test_rejects_bad_buy_type(self):
        pytest.importorskip("czsc")
        with pytest.raises(ValueError, match="buy_type"):
            detect_chanlun_buy(_make_bars(80), buy_type="buy9")  # type: ignore[arg-type]


class TestScreenValidation:
    def test_rejects_mixed_custom_universe(self, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("czsc")
        from backtest import chanlun_screen as mod

        monkeypatch.setattr(
            mod,
            "resolve_universe_codes",
            lambda universe, codes=None: ["600036.SH", "AAPL"],
        )
        with pytest.raises(ValueError, match="cannot mix"):
            screen_chanlun_buy(universe="custom", codes=["600036.SH", "AAPL"])

    def test_screen_ranks_and_attaches_charts(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pytest.importorskip("czsc")
        from backtest import chanlun_screen as mod

        df_a = _make_bars(80, start="2024-01-01")
        df_b = _make_bars(80, start="2024-01-02")

        monkeypatch.setattr(
            mod, "resolve_universe_codes", lambda universe, codes=None: ["AAA.SH", "BBB.SH"]
        )
        monkeypatch.setattr(
            mod,
            "_fetch_ohlcv",
            lambda codes, market, **kwargs: (
                {"AAA.SH": df_a, "BBB.SH": df_b},
                "test",
            ),
        )
        monkeypatch.setattr(
            mod,
            "_resolve_security_names",
            lambda codes, market: {"AAA.SH": "Alpha", "BBB.SH": "Beta"},
        )

        def fake_detect(df, **kwargs):
            # Newer signal for BBB (starts 2024-01-02).
            start = pd.Timestamp(df["trade_date"].iloc[0]).strftime("%Y-%m-%d")
            if start == "2024-01-02":
                return {
                    "signal_date": "2024-03-20",
                    "buy_type": "buy3",
                    "buy_label": "三买",
                    "signal_detail": "三买_均线新高_任意_0",
                    "close": 108.0,
                    "zg": 105.0,
                    "zd": 100.0,
                    "bi_high": 109.0,
                    "bi_low": 101.0,
                    "days_since_signal": 0,
                }
            return {
                "signal_date": "2024-03-18",
                "buy_type": "buy3",
                "buy_label": "三买",
                "signal_detail": "三买_均线底分_任意_0",
                "close": 107.0,
                "zg": 104.0,
                "zd": 99.0,
                "bi_high": 108.0,
                "bi_low": 100.0,
                "days_since_signal": 1,
            }

        monkeypatch.setattr(mod, "detect_chanlun_buy", fake_detect)

        out = screen_chanlun_buy(
            universe="csi300", buy_type="buy3", signal_freshness=10, top=10
        )
        assert out["count"] == 2
        assert out["buy_label"] == "三买"
        assert out["results"][0]["code"] == "BBB.SH"
        assert out["results"][0]["name"] == "Beta"
        assert out["results"][0]["sparkline"]
        assert out["results"][0]["bars"]
        assert out["results"][1]["code"] == "AAA.SH"
