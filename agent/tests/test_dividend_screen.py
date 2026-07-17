"""Tests for backtest/dividend_screen.py filter helpers."""

import pandas as pd
import pytest

from backtest.dividend_screen import (
    apply_dividend_filters,
    _format_tushare_failure,
    _is_tushare_hard_error,
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


class TestTushareErrorHelpers:
    def test_rate_limit_is_hard_error(self):
        exc = Exception(
            "抱歉，您访问接口(daily_basic)频率超限(1次/分钟)，"
            "具体频次详情：https://tushare.pro/document/1?doc_id=108。"
        )
        assert _is_tushare_hard_error(exc) is True
        msg = _format_tushare_failure("daily_basic", exc)
        assert "rate-limited" in msg
        assert "1次/分钟" in msg

    def test_permission_denied_is_hard_error(self):
        exc = Exception(
            "抱歉，您没有接口(index_weight)访问权限，"
            "权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。"
        )
        assert _is_tushare_hard_error(exc) is True
        assert "permission denied" in _format_tushare_failure("index_weight", exc)

    def test_generic_network_error_is_soft(self):
        assert _is_tushare_hard_error(Exception("Connection timed out")) is False


class TestFetchAShareBasics:
    def test_rate_limit_falls_back_to_akshare(self, monkeypatch: pytest.MonkeyPatch):
        from backtest import dividend_screen as mod

        monkeypatch.setenv("TUSHARE_TOKEN", "real-looking-token-not-placeholder")
        monkeypatch.setattr(mod, "_DAILY_BASIC_RETRY_GAP_SEC", 0)

        class _FakePro:
            def trade_cal(self, **_kwargs):
                raise Exception("抱歉，您访问接口(trade_cal)频率超限(1次/分钟)")

            def daily_basic(self, **_kwargs):
                raise Exception("抱歉，您访问接口(daily_basic)频率超限(1次/分钟)")

            def stock_basic(self, **_kwargs):
                return pd.DataFrame(columns=["ts_code", "name"])

        class _FakeTs:
            @staticmethod
            def pro_api(_token):
                return _FakePro()

        class _FakeAk:
            @staticmethod
            def stock_fhps_em(date=None):
                return pd.DataFrame(
                    [
                        {
                            "代码": "600036",
                            "名称": "招商银行",
                            "现金分红-股息率": 0.045,
                        }
                    ]
                )

        monkeypatch.setitem(__import__("sys").modules, "tushare", _FakeTs())
        monkeypatch.setitem(__import__("sys").modules, "akshare", _FakeAk())
        monkeypatch.setattr(mod, "_tushare_token", lambda: "real-looking-token-not-placeholder")
        monkeypatch.setattr(mod, "_fhps_report_dates", lambda: ["20251231"])

        frame, td, source = mod._fetch_a_share_basics(["600036.SH"])
        assert td == "20251231"
        assert source.startswith("akshare.stock_fhps_em")
        assert float(frame.iloc[0]["dv_ttm"]) == pytest.approx(4.5)

    def test_rate_limit_surfaces_when_akshare_also_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from backtest import dividend_screen as mod

        monkeypatch.setenv("TUSHARE_TOKEN", "real-looking-token-not-placeholder")
        monkeypatch.setattr(mod, "_DAILY_BASIC_RETRY_GAP_SEC", 0)

        class _FakePro:
            def trade_cal(self, **_kwargs):
                raise Exception("抱歉，您访问接口(trade_cal)频率超限(1次/分钟)")

            def daily_basic(self, **_kwargs):
                raise Exception("抱歉，您访问接口(daily_basic)频率超限(1次/分钟)")

            def stock_basic(self, **_kwargs):
                return pd.DataFrame(columns=["ts_code", "name"])

        class _FakeTs:
            @staticmethod
            def pro_api(_token):
                return _FakePro()

        class _FakeAk:
            @staticmethod
            def stock_fhps_em(date=None):
                raise RuntimeError("eastmoney down")

        monkeypatch.setitem(__import__("sys").modules, "tushare", _FakeTs())
        monkeypatch.setitem(__import__("sys").modules, "akshare", _FakeAk())
        monkeypatch.setattr(mod, "_tushare_token", lambda: "real-looking-token-not-placeholder")
        monkeypatch.setattr(mod, "_fhps_report_dates", lambda: ["20251231"])

        with pytest.raises(RuntimeError, match="rate-limited.*AKShare fallback"):
            mod._fetch_a_share_basics(["600036.SH"])

    def test_no_token_uses_akshare(self, monkeypatch: pytest.MonkeyPatch):
        from backtest import dividend_screen as mod

        monkeypatch.setattr(mod, "_tushare_token", lambda: None)

        class _FakeAk:
            @staticmethod
            def stock_fhps_em(date=None):
                return pd.DataFrame(
                    [
                        {
                            "代码": "000001",
                            "名称": "平安银行",
                            "现金分红-股息率": 3.2,  # already percent
                        }
                    ]
                )

        monkeypatch.setitem(__import__("sys").modules, "akshare", _FakeAk())
        monkeypatch.setattr(mod, "_fhps_report_dates", lambda: ["20241231"])

        frame, td, source = mod._fetch_a_share_basics(["000001.SZ"])
        assert td == "20241231"
        assert "akshare" in source
        assert float(frame.iloc[0]["dv_ttm"]) == pytest.approx(3.2)

    def test_skips_thin_midyear_for_annual(self, monkeypatch: pytest.MonkeyPatch):
        """Early-season *0630 with 1 name must not beat a full *1231 file."""
        from backtest import dividend_screen as mod

        monkeypatch.setattr(mod, "_tushare_token", lambda: None)
        monkeypatch.setattr(
            mod,
            "_fhps_report_dates",
            lambda: ["20260630", "20251231"],
        )

        universe = [f"{i:06d}.SH" for i in range(600000, 600030)]

        class _FakeAk:
            @staticmethod
            def stock_fhps_em(date=None):
                if date == "20260630":
                    return pd.DataFrame(
                        [
                            {
                                "代码": "600000",
                                "名称": "Thin",
                                "现金分红-股息率": 0.01,
                            }
                        ]
                    )
                return pd.DataFrame(
                    [
                        {
                            "代码": f"{i:06d}",
                            "名称": f"N{i}",
                            "现金分红-股息率": 0.04,
                        }
                        for i in range(600000, 600030)
                    ]
                )

        monkeypatch.setitem(__import__("sys").modules, "akshare", _FakeAk())

        frame, td, source = mod._fetch_a_share_basics(universe)
        assert td == "20251231"
        assert len(frame) == 30
        assert (frame["dv_ttm"] >= 3.0).all()
        assert "20251231" in source

    def test_walks_back_when_latest_day_empty(self, monkeypatch: pytest.MonkeyPatch):
        from backtest import dividend_screen as mod

        monkeypatch.setattr(mod, "_DAILY_BASIC_RETRY_GAP_SEC", 0)
        # Keep "today" first so the empty→prior walk path is exercised.
        monkeypatch.setattr(mod, "_defer_today_trade_date", lambda dates: dates)

        class _FakePro:
            def trade_cal(self, **_kwargs):
                return pd.DataFrame({"cal_date": ["20260717", "20260716", "20260715"]})

            def daily_basic(self, trade_date=None, **_kwargs):
                if trade_date == "20260717":
                    return pd.DataFrame()
                if trade_date == "20260716":
                    return pd.DataFrame(
                        [
                            {
                                "ts_code": "600036.SH",
                                "trade_date": "20260716",
                                "close": 35.0,
                                "dv_ttm": 4.5,
                                "pe_ttm": 6.0,
                                "pb": 0.9,
                                "total_mv": 1_000_000,
                            }
                        ]
                    )
                return pd.DataFrame()

            def stock_basic(self, **_kwargs):
                return pd.DataFrame([{"ts_code": "600036.SH", "name": "招商银行"}])

        class _FakeTs:
            @staticmethod
            def pro_api(_token):
                return _FakePro()

        monkeypatch.setitem(__import__("sys").modules, "tushare", _FakeTs())
        monkeypatch.setattr(mod, "_tushare_token", lambda: "real-looking-token-not-placeholder")

        frame, td, source = mod._fetch_a_share_basics_tushare(["600036.SH"])
        assert td == "20260716"
        assert source.startswith("tushare.daily_basic")
        assert float(frame.iloc[0]["dv_ttm"]) == 4.5

    def test_defers_today_so_first_call_hits_prior_session(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from backtest import dividend_screen as mod

        monkeypatch.setattr(mod, "_DAILY_BASIC_RETRY_GAP_SEC", 0)
        calls: list[str] = []

        class _FakePro:
            def trade_cal(self, **_kwargs):
                return pd.DataFrame({"cal_date": ["20260717", "20260716", "20260715"]})

            def daily_basic(self, trade_date=None, **_kwargs):
                calls.append(trade_date)
                return pd.DataFrame(
                    [
                        {
                            "ts_code": "600036.SH",
                            "trade_date": trade_date,
                            "close": 35.0,
                            "dv_ttm": 4.5,
                            "pe_ttm": 6.0,
                            "pb": 0.9,
                            "total_mv": 1_000_000,
                        }
                    ]
                )

            def stock_basic(self, **_kwargs):
                return pd.DataFrame([{"ts_code": "600036.SH", "name": "招商银行"}])

        class _FakeTs:
            @staticmethod
            def pro_api(_token):
                return _FakePro()

        monkeypatch.setitem(__import__("sys").modules, "tushare", _FakeTs())
        monkeypatch.setattr(mod, "_tushare_token", lambda: "real-looking-token-not-placeholder")
        monkeypatch.setattr(
            mod,
            "datetime",
            type(
                "DT",
                (),
                {
                    "now": staticmethod(
                        lambda tz=None: __import__("datetime").datetime(
                            2026, 7, 17, 22, 0, tzinfo=tz
                        )
                    ),
                    "timedelta": __import__("datetime").timedelta,
                },
            ),
        )

        frame, td, source = mod._fetch_a_share_basics_tushare(["600036.SH"])
        assert calls[0] == "20260716"
        assert td == "20260716"
        assert float(frame.iloc[0]["dv_ttm"]) == 4.5


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
