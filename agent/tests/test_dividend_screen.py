"""Tests for backtest/dividend_screen.py filter helpers."""

import pandas as pd
import pytest

from backtest.dividend_screen import (
    apply_dividend_filters,
    _normalize_a_share_code,
    _normalize_us_code,
    _normalize_yield_pct,
    _rows_from_frame,
)


class TestNormalizeHelpers:
    def test_a_share_codes(self):
        assert _normalize_a_share_code("600519") == "600519.SH"
        assert _normalize_a_share_code("000001.sz") == "000001.SZ"
        assert _normalize_a_share_code("300750") == "300750.SZ"

    def test_us_codes(self):
        assert _normalize_us_code("aapl") == "AAPL"
        assert _normalize_us_code("BRK.B") == "BRK-B"
        assert _normalize_us_code("AAPL.US") == "AAPL"

    def test_yield_normalization(self):
        assert _normalize_yield_pct(0.035) == pytest.approx(3.5)
        assert _normalize_yield_pct(3.5) == pytest.approx(3.5)
        assert _normalize_yield_pct(None) is None
        assert _normalize_yield_pct(0) is None
        assert _normalize_yield_pct(-1) is None


class TestApplyDividendFilters:
    def _frame(self):
        return pd.DataFrame(
            [
                {"ts_code": "A", "name": "High", "dv_ttm": 5.5, "pe_ttm": 8.0, "total_mv": 5_000_000},
                {"ts_code": "B", "name": "Mid", "dv_ttm": 3.2, "pe_ttm": 12.0, "total_mv": 800_000},
                {"ts_code": "C", "name": "Low", "dv_ttm": 1.5, "pe_ttm": 20.0, "total_mv": 2_000_000},
                {"ts_code": "D", "name": "Trap", "dv_ttm": 18.0, "pe_ttm": -5.0, "total_mv": 1_000_000},
                {"ts_code": "E", "name": "Expensive", "dv_ttm": 4.0, "pe_ttm": 40.0, "total_mv": 3_000_000},
            ]
        )

    def test_min_yield_and_rank(self):
        out = apply_dividend_filters(self._frame(), min_yield=3.0)
        assert list(out["ts_code"]) == ["D", "A", "E", "B"]

    def test_max_yield_filters_traps(self):
        out = apply_dividend_filters(self._frame(), min_yield=3.0, max_yield=10.0)
        assert "D" not in set(out["ts_code"])
        assert list(out["ts_code"]) == ["A", "E", "B"]

    def test_max_pe(self):
        out = apply_dividend_filters(self._frame(), min_yield=3.0, max_pe=15.0)
        # D has negative PE → excluded; E has PE 40 → excluded
        assert list(out["ts_code"]) == ["A", "B"]

    def test_min_market_cap_yi(self):
        # total_mv is 万元; 100 亿元 => 1_000_000 万元
        out = apply_dividend_filters(
            self._frame(),
            min_yield=3.0,
            min_market_cap=100.0,
            market_cap_is_wan=True,
        )
        assert list(out["ts_code"]) == ["D", "A", "E"]


class TestRowsFromFrame:
    def test_cny_yi_conversion(self):
        frame = pd.DataFrame(
            [
                {
                    "ts_code": "600036.SH",
                    "name": "招商银行",
                    "dv_ttm": 4.56,
                    "pe_ttm": 6.2,
                    "pb": 0.9,
                    "total_mv": 1_234_000,  # 万元 → 123.4 亿元
                    "close": 35.2,
                }
            ]
        )
        rows = _rows_from_frame(frame, market_cap_unit="CNY_yi", top=10)
        assert rows[0]["code"] == "600036.SH"
        assert rows[0]["dividend_yield"] == 4.56
        assert rows[0]["market_cap"] == 123.4
