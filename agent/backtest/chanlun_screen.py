"""Chanlun (缠论) buy-point stock screener via czsc.

Detects first / second / third buy points on daily bars using the czsc library
(fractal → bi → zhongshu → buy signals). Used by the ``/chanlun`` API endpoint.

Universe resolution and OHLCV fetch reuse ``buy_point_screen`` helpers.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import pandas as pd

from backtest.buy_point_screen import (
    _fetch_ohlcv,
    _fmt_date,
    _normalize_ohlcv,
    _ohlcv_bars,
    _resolve_security_names,
    _sparkline_series,
)
from backtest.dividend_screen import (
    Universe,
    _infer_a_share,
    _normalize_a_share_code,
    _normalize_us_code,
    resolve_universe_codes,
)

logger = logging.getLogger(__name__)

BuyType = Literal["buy1", "buy2", "buy3"]

_BUY_LABELS: dict[BuyType, str] = {
    "buy1": "一买",
    "buy2": "二买",
    "buy3": "三买",
}

# Chanlun structure needs more history than the right-side buy screen.
_CALENDAR_PAD_DAYS = 400
_SINA_DATALEN = 250
_MIN_BARS = 60


def _require_czsc():
    try:
        from czsc import CZSC, Freq, RawBar, ZS  # noqa: F401
        from czsc.signals.cxt import (  # noqa: F401
            cxt_first_buy_V221126,
            cxt_second_bs_V230320,
            cxt_third_bs_V230319,
            cxt_third_buy_V230228,
        )
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            "czsc is required for Chanlun screening. Install with: pip install czsc"
        ) from exc


def _df_to_bars(df: pd.DataFrame, symbol: str = "X") -> list:
    from czsc import Freq, RawBar

    bars_df = _normalize_ohlcv(df)
    out = []
    for i, row in enumerate(bars_df.itertuples(index=False)):
        dt = pd.Timestamp(row.trade_date).to_pydatetime()
        out.append(
            RawBar(
                symbol=symbol,
                id=i,
                dt=dt,
                freq=Freq.D,
                open=float(row.open),
                close=float(row.close),
                high=float(row.high),
                low=float(row.low),
                vol=float(row.volume),
                amount=0.0,
            )
        )
    return out


def _collect_signals(c, *, buy_type: BuyType, ma_period: int) -> dict[str, str]:
    from czsc.signals.cxt import (
        cxt_first_buy_V221126,
        cxt_second_bs_V230320,
        cxt_third_bs_V230319,
        cxt_third_buy_V230228,
    )

    s: dict[str, str] = {}
    if buy_type == "buy1":
        s.update(cxt_first_buy_V221126(c, di=1))
    elif buy_type == "buy2":
        s.update(cxt_second_bs_V230320(c, di=1, ma_type="SMA", timeperiod=ma_period))
    else:
        s.update(cxt_third_bs_V230319(c, di=1, ma_type="SMA", timeperiod=ma_period))
        s.update(cxt_third_buy_V230228(c, di=1))
    return {str(k): str(v) for k, v in s.items()}


def _match_buy(signals: dict[str, str], buy_type: BuyType) -> tuple[bool, str]:
    label = _BUY_LABELS[buy_type]
    for _key, value in signals.items():
        if label in value and "其他" not in value:
            return True, value
    return False, ""


def _extract_zs_levels(c) -> tuple[float | None, float | None]:
    from czsc import ZS

    bi_list = list(c.bi_list or [])
    if len(bi_list) < 3:
        return None, None
    start = len(bi_list) - 3
    stop = max(len(bi_list) - 12, -1)
    for i in range(start, stop, -1):
        try:
            zs = ZS(bis=bi_list[i : i + 3])
        except Exception:  # noqa: BLE001
            continue
        if getattr(zs, "is_valid", False):
            zg = getattr(zs, "zg", None)
            zd = getattr(zs, "zd", None)
            try:
                return (
                    round(float(zg), 4) if zg is not None else None,
                    round(float(zd), 4) if zd is not None else None,
                )
            except (TypeError, ValueError):
                return None, None
    return None, None


def detect_chanlun_buy(
    df: pd.DataFrame,
    *,
    buy_type: BuyType = "buy3",
    signal_freshness: int = 10,
    ma_period: int = 34,
    symbol: str = "X",
) -> dict[str, Any] | None:
    """Detect the most recent Chanlun buy onset on a single OHLCV series.

    Scans bars with czsc, records the last onset of the requested buy type,
    and returns it only when that onset falls within ``signal_freshness``
    sessions of the latest bar.
    """
    _require_czsc()
    from czsc import CZSC

    if buy_type not in _BUY_LABELS:
        raise ValueError(f"buy_type must be one of {sorted(_BUY_LABELS)}")
    if signal_freshness < 1:
        raise ValueError("signal_freshness must be >= 1")
    if ma_period < 2:
        raise ValueError("ma_period must be >= 2")

    bars_df = _normalize_ohlcv(df)
    bars = _df_to_bars(bars_df, symbol=symbol)
    n = len(bars)
    if n < _MIN_BARS:
        return None

    # Prefer original trade_date column — czsc may rewrite RawBar.dt with tz offsets.
    trade_dates = [_fmt_date(v) for v in bars_df["trade_date"].tolist()]

    # Build structure on older bars in one shot; only walk a short tail to find
    # onsets inside the freshness window (scan_pad > freshness so a signal that
    # was already active at warmup is necessarily older than freshness).
    scan_pad = max(signal_freshness + 25, 30)
    warmup = max(_MIN_BARS, n - scan_pad)
    if warmup >= n:
        warmup = n - 1

    c = CZSC(bars[:warmup])
    prev_active = False
    try:
        prev_active, _ = _match_buy(
            _collect_signals(c, buy_type=buy_type, ma_period=ma_period),
            buy_type,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("chanlun_screen: warmup signals failed: %s", exc)
        prev_active = False

    last_onset: dict[str, Any] | None = None

    for bar in bars[warmup:]:
        try:
            c.update(bar)
            active, detail = _match_buy(
                _collect_signals(c, buy_type=buy_type, ma_period=ma_period),
                buy_type,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("chanlun_screen: bar update failed at %s: %s", bar.dt, exc)
            continue

        if active and not prev_active:
            zg, zd = _extract_zs_levels(c)
            bi = c.bi_list[-1] if c.bi_list else None
            bar_id = int(bar.id)
            last_onset = {
                "signal_date": trade_dates[bar_id],
                "buy_type": buy_type,
                "buy_label": _BUY_LABELS[buy_type],
                "signal_detail": detail,
                "close": round(float(bar.close), 4),
                "zg": zg,
                "zd": zd,
                "bi_high": round(float(bi.high), 4) if bi is not None else None,
                "bi_low": round(float(bi.low), 4) if bi is not None else None,
                "_bar_id": bar_id,
            }
        prev_active = active

    if last_onset is None:
        return None

    days_since = n - 1 - int(last_onset.pop("_bar_id"))
    if days_since > signal_freshness - 1:
        return None
    last_onset["days_since_signal"] = int(days_since)
    return last_onset


def screen_chanlun_buy(
    *,
    universe: Universe = "csi300",
    codes: list[str] | None = None,
    buy_type: BuyType = "buy3",
    signal_freshness: int = 10,
    ma_period: int = 34,
    top: int = 50,
) -> dict[str, Any]:
    """Screen a universe for fresh Chanlun buy points."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _require_czsc()

    if buy_type not in _BUY_LABELS:
        raise ValueError(f"buy_type must be one of {sorted(_BUY_LABELS)}")
    if signal_freshness < 1 or signal_freshness > 60:
        raise ValueError("signal_freshness must be between 1 and 60")
    if ma_period < 2 or ma_period > 120:
        raise ValueError("ma_period must be between 2 and 120")
    if top < 1 or top > 500:
        raise ValueError("top must be between 1 and 500")

    resolved = resolve_universe_codes(universe, codes)
    if not resolved:
        raise ValueError("Resolved universe is empty")

    if universe == "sp500":
        is_a_share = False
    elif universe == "csi300":
        is_a_share = True
    else:
        flags = [_infer_a_share(c) for c in resolved]
        if any(flags) and not all(flags):
            raise ValueError(
                "custom universe cannot mix A-shares and US tickers in one request; "
                "screen each market separately"
            )
        is_a_share = bool(flags[0])

    if is_a_share:
        tickers = [_normalize_a_share_code(c) for c in resolved]
        market: Literal["a_share", "us_equity"] = "a_share"
    else:
        tickers = [_normalize_us_code(c) for c in resolved]
        market = "us_equity"

    logger.info(
        "chanlun_screen: fetching OHLCV for %d names (%s, buy_type=%s)",
        len(tickers),
        market,
        buy_type,
    )
    price_map, source = _fetch_ohlcv(
        tickers,
        market=market,
        calendar_pad_days=_CALENDAR_PAD_DAYS,
        sina_datalen=_SINA_DATALEN,
    )
    logger.info(
        "chanlun_screen: fetched %d/%d via %s; detecting %s",
        len(price_map),
        len(tickers),
        source,
        buy_type,
    )

    matched: list[dict[str, Any]] = []
    items = list(price_map.items())
    workers = min(8, max(1, len(items)))

    def _one(code: str, frame: pd.DataFrame) -> dict[str, Any] | None:
        try:
            signal = detect_chanlun_buy(
                frame,
                buy_type=buy_type,
                signal_freshness=signal_freshness,
                ma_period=ma_period,
                symbol=code,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("chanlun_screen: detect failed for %s: %s", code, exc)
            return None
        if signal is None:
            return None
        return {
            "code": code,
            "name": "",
            "sparkline": _sparkline_series(frame, limit=60),
            "bars": _ohlcv_bars(frame, limit=120),
            **signal,
        }

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, code, frame) for code, frame in items]
        for fut in as_completed(futures):
            done += 1
            if done % 50 == 0 or done == len(futures):
                logger.info(
                    "chanlun_screen: detect progress %d/%d",
                    done,
                    len(futures),
                )
            row = fut.result()
            if row is not None:
                matched.append(row)

    matched.sort(
        key=lambda row: (row["signal_date"], -(row["days_since_signal"])),
        reverse=True,
    )
    results = matched[:top]
    if results:
        name_map = _resolve_security_names(
            [row["code"] for row in results], market=market
        )
        for row in results:
            row["name"] = name_map.get(row["code"], "") or ""

    trade_date = ""
    if price_map:
        lasts = []
        for frame in price_map.values():
            bars = _normalize_ohlcv(frame)
            if not bars.empty:
                lasts.append(_fmt_date(bars["trade_date"].iloc[-1]))
        if lasts:
            trade_date = max(lasts)

    warning = None
    if len(price_map) < len(resolved):
        warning = (
            f"Only fetched OHLCV for {len(price_map)}/{len(resolved)} names "
            f"via {source}; matches may be incomplete."
        )

    logger.info(
        "chanlun_screen: done buy_type=%s matched=%d showing=%d",
        buy_type,
        len(matched),
        len(results),
    )
    return {
        "universe": universe,
        "market": market,
        "trade_date": trade_date,
        "buy_type": buy_type,
        "buy_label": _BUY_LABELS[buy_type],
        "signal_freshness": signal_freshness,
        "ma_period": ma_period,
        "universe_size": len(resolved),
        "fetched": len(price_map),
        "matched": len(matched),
        "count": len(results),
        "source": source,
        "warning": warning,
        "results": results,
    }
