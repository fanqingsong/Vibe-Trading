"""Right-side buy-point stock screener.

Detects breakout-of-prior-high → pullback-that-holds → reclaim patterns.
Used by the ``/buy-points`` API endpoint.

Rule (daily bars)
-----------------
1. Prior high = max(high) over ``prior_high_lookback`` sessions ending
   ``prior_high_exclude`` sessions before the breakout bar.
2. Breakout: close breaks above that prior high.
3. Pullback: within 3–15 sessions after breakout, price dips but the
   pullback low stays ≥ prior_high × (1 − hold_tolerance).
4. Reclaim (right-side confirm): first subsequent close back ≥ prior high.
5. Optional volume confirm: breakout volume ≥ 20-day average × volume_mult.
6. Only signals whose reclaim bar falls in the last ``signal_freshness``
   sessions are returned.

Universe resolution reuses ``dividend_screen.resolve_universe_codes``.
OHLCV comes from the standard loader registry (Tushare/AKShare for
A-shares, yfinance for US).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Literal

import pandas as pd

from backtest.dividend_screen import (
    Universe,
    _infer_a_share,
    _normalize_a_share_code,
    _normalize_us_code,
    resolve_universe_codes,
)

logger = logging.getLogger(__name__)

_FETCH_WORKERS = 8
# ~85 trading sessions needed; ~130 calendar days covers lookback+pullback+buffer.
_CALENDAR_PAD_DAYS = 130
# Prefer bulk Tushare-by-date (≈1 call/day for all names) over per-code daily
# (50/min quota). Free HTTP/TCP sources fill gaps when bulk is unavailable.
_A_SHARE_BULK_CHAIN = ("tushare_bulk", "mootdx", "akshare", "tushare")
_A_SHARE_SMALL_CHAIN = ("akshare", "mootdx", "tushare")  # custom / tiny lists
_US_SCREEN_CHAIN = ("yfinance", "akshare")
_TUSHARE_MIN_INTERVAL_SEC = 1.22  # stay under ~50 calls/min
_AKSHARE_RETRIES = 2
_SMALL_UNIVERSE = 20
_BULK_CACHE_TTL_SEC = 45 * 60
_bulk_cache: dict[str, tuple[float, dict[str, pd.DataFrame]]] = {}
_BULK_DISK_DIR = None  # lazy Path


def _bulk_disk_dir():
    global _BULK_DISK_DIR
    if _BULK_DISK_DIR is None:
        import os
        from pathlib import Path

        _BULK_DISK_DIR = Path(
            os.getenv("VIBE_BUY_POINT_CACHE", "/tmp/vibe_buy_point_cache")
        )
        _BULK_DISK_DIR.mkdir(parents=True, exist_ok=True)
    return _BULK_DISK_DIR


def detect_right_side_buy(
    df: pd.DataFrame,
    *,
    prior_high_lookback: int = 60,
    prior_high_exclude: int = 5,
    min_pullback_days: int = 3,
    max_pullback_days: int = 15,
    hold_tolerance: float = 0.02,
    signal_freshness: int = 10,
    volume_ma: int = 20,
    volume_mult: float = 1.2,
    require_volume: bool = True,
) -> dict[str, Any] | None:
    """Detect the most recent right-side buy on a single OHLCV series.

    ``df`` must contain ``high``, ``low``, ``close``, ``volume`` and a
    chronological ``trade_date`` column (or DatetimeIndex).
    Returns a signal dict or ``None`` when no fresh pattern matches.
    """
    bars = _normalize_ohlcv(df)
    n = len(bars)
    # Need prior-high window + exclude gap + breakout + at least min pullback/reclaim.
    min_bars = prior_high_lookback + prior_high_exclude + min_pullback_days + 1
    if n < min_bars:
        return None

    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    volume = bars["volume"].to_numpy(dtype=float)
    dates = bars["trade_date"]

    freshness_start = max(0, n - signal_freshness)
    best: dict[str, Any] | None = None

    for signal_idx in range(freshness_start, n):
        for days_after in range(min_pullback_days, max_pullback_days + 1):
            breakout_idx = signal_idx - days_after
            prior_end = breakout_idx - prior_high_exclude
            prior_start = prior_end - prior_high_lookback
            if prior_start < 0 or breakout_idx < 0:
                continue

            prior_high = float(high[prior_start:prior_end].max())
            if not (prior_high > 0) or close[breakout_idx] <= prior_high:
                continue

            vol_ratio: float | None = None
            if breakout_idx >= volume_ma:
                avg_vol = float(volume[breakout_idx - volume_ma : breakout_idx].mean())
                if avg_vol > 0:
                    vol_ratio = round(float(volume[breakout_idx]) / avg_vol, 4)
            if require_volume:
                if vol_ratio is None or vol_ratio < volume_mult:
                    continue

            # Bars strictly between breakout and reclaim.
            if signal_idx <= breakout_idx + 1:
                continue
            pullback_low = float(low[breakout_idx + 1 : signal_idx].min())
            # Must actually pull back off the breakout close.
            if pullback_low >= close[breakout_idx]:
                continue
            floor = prior_high * (1.0 - hold_tolerance)
            if pullback_low < floor:
                continue

            # Right-side confirm: close back at/above prior high after a pullback.
            if close[signal_idx] < prior_high:
                continue
            # Classic reclaim (prev close still below) OR support-test bounce
            # (prev bar probed the breakout shelf while closes stayed elevated).
            probed = (
                close[signal_idx - 1] < prior_high
                or float(low[signal_idx - 1]) <= prior_high * (1.0 + hold_tolerance)
            )
            if not probed:
                continue
            # Prefer the first confirmation after the pullback trough.
            trough_offset = int(low[breakout_idx + 1 : signal_idx].argmin())
            trough_idx = breakout_idx + 1 + trough_offset
            if signal_idx <= trough_idx:
                continue
            earlier = False
            for j in range(trough_idx + 1, signal_idx):
                prev_probed = (
                    close[j - 1] < prior_high
                    or float(low[j - 1]) <= prior_high * (1.0 + hold_tolerance)
                )
                if close[j] >= prior_high and prev_probed:
                    earlier = True
                    break
            if earlier:
                continue

            breakout_pct = round(
                (float(close[breakout_idx]) - prior_high) / prior_high * 100.0,
                4,
            )
            days_since = n - 1 - signal_idx
            candidate = {
                "signal_date": _fmt_date(dates.iloc[signal_idx]),
                "breakout_date": _fmt_date(dates.iloc[breakout_idx]),
                "prior_high": round(prior_high, 4),
                "pullback_low": round(pullback_low, 4),
                "breakout_close": round(float(close[breakout_idx]), 4),
                "close": round(float(close[signal_idx]), 4),
                "breakout_pct": breakout_pct,
                "volume_ratio": vol_ratio,
                "days_since_signal": int(days_since),
                "days_after_breakout": int(days_after),
            }
            # Prefer the newest signal_date; ties keep higher breakout_pct.
            if best is None or candidate["signal_date"] > best["signal_date"] or (
                candidate["signal_date"] == best["signal_date"]
                and candidate["breakout_pct"] > best["breakout_pct"]
            ):
                best = candidate

    return best


def screen_right_side_buy(
    *,
    universe: Universe = "csi300",
    codes: list[str] | None = None,
    prior_high_lookback: int = 60,
    prior_high_exclude: int = 5,
    min_pullback_days: int = 3,
    max_pullback_days: int = 15,
    hold_tolerance: float = 0.02,
    signal_freshness: int = 10,
    volume_ma: int = 20,
    volume_mult: float = 1.2,
    require_volume: bool = True,
    top: int = 50,
) -> dict[str, Any]:
    """Screen a universe for fresh right-side buy points."""
    if prior_high_lookback < 10:
        raise ValueError("prior_high_lookback must be >= 10")
    if min_pullback_days < 1 or max_pullback_days < min_pullback_days:
        raise ValueError("pullback window invalid: need 1 <= min <= max")
    if not (0 <= hold_tolerance <= 0.2):
        raise ValueError("hold_tolerance must be between 0 and 0.2")
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
        market = "a_share"
    else:
        tickers = [_normalize_us_code(c) for c in resolved]
        market = "us_equity"

    price_map, source = _fetch_ohlcv(tickers, market=market)
    detect_kwargs = dict(
        prior_high_lookback=prior_high_lookback,
        prior_high_exclude=prior_high_exclude,
        min_pullback_days=min_pullback_days,
        max_pullback_days=max_pullback_days,
        hold_tolerance=hold_tolerance,
        signal_freshness=signal_freshness,
        volume_ma=volume_ma,
        volume_mult=volume_mult,
        require_volume=require_volume,
    )

    matched: list[dict[str, Any]] = []
    for code, frame in price_map.items():
        signal = detect_right_side_buy(frame, **detect_kwargs)
        if signal is None:
            continue
        matched.append(
            {
                "code": code,
                "name": "",
                **signal,
            }
        )

    matched.sort(
        key=lambda row: (row["signal_date"], row["breakout_pct"]),
        reverse=True,
    )
    results = matched[:top]
    trade_date = ""
    if price_map:
        # Latest bar date across successfully fetched series.
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

    return {
        "universe": universe,
        "market": market,
        "trade_date": trade_date,
        "prior_high_lookback": prior_high_lookback,
        "prior_high_exclude": prior_high_exclude,
        "min_pullback_days": min_pullback_days,
        "max_pullback_days": max_pullback_days,
        "hold_tolerance": hold_tolerance,
        "signal_freshness": signal_freshness,
        "require_volume": require_volume,
        "volume_mult": volume_mult,
        "universe_size": len(resolved),
        "fetched": len(price_map),
        "matched": len(matched),
        "count": len(results),
        "source": source,
        "warning": warning,
        "results": results,
    }


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "high", "low", "close", "volume"])

    frame = df.copy()
    if "trade_date" not in frame.columns:
        if isinstance(frame.index, pd.DatetimeIndex) or "trade_date" in list(frame.index.names or []):
            frame = frame.reset_index()
            if "trade_date" not in frame.columns and "index" in frame.columns:
                frame = frame.rename(columns={"index": "trade_date"})
        else:
            raise ValueError("OHLCV frame needs a trade_date column or DatetimeIndex")

    required = ("high", "low", "close", "volume")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")

    out = frame[["trade_date", "high", "low", "close", "volume"]].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    out = out.dropna(subset=["high", "low", "close"])
    out = out.sort_values("trade_date").reset_index(drop=True)
    out["volume"] = out["volume"].fillna(0.0)
    return out


def _fmt_date(value: Any) -> str:
    ts = pd.Timestamp(value)
    return ts.strftime("%Y-%m-%d")


def _fetch_ohlcv(
    codes: list[str],
    *,
    market: Literal["a_share", "us_equity"],
) -> tuple[dict[str, pd.DataFrame], str]:
    """Fetch daily OHLCV for many tickers; returns (code→df, source label).

    A-share screens prefer Tushare ``daily(trade_date=…)`` bulk pulls so a
    CSI300 scan costs ~N trading-day calls instead of 300 per-code calls.
    """
    import time

    from backtest.loaders.registry import LOADER_REGISTRY, _ensure_registered

    _ensure_registered()

    end = datetime.now()
    start = end - timedelta(days=_CALENDAR_PAD_DAYS)
    start_date = start.strftime("%Y-%m-%d")
    end_date = end.strftime("%Y-%m-%d")
    fields = None  # do not request daily_basic extras (burns Tushare quota)

    if market == "a_share":
        chain = (
            _A_SHARE_SMALL_CHAIN
            if len(codes) <= _SMALL_UNIVERSE
            else _A_SHARE_BULK_CHAIN
        )
    else:
        chain = _US_SCREEN_CHAIN

    results: dict[str, pd.DataFrame] = {}
    sources_used: list[str] = []
    remaining = list(codes)
    skip_tushare_per_code = False

    for source_name in chain:
        if not remaining:
            break
        if source_name == "tushare" and skip_tushare_per_code:
            continue

        before = len(results)
        got: dict[str, pd.DataFrame] = {}

        if source_name == "tushare_bulk":
            got, aborted = _fetch_a_share_tushare_bulk(
                remaining, start_date=start_date, end_date=end_date
            )
            if aborted:
                skip_tushare_per_code = True
        else:
            if source_name not in LOADER_REGISTRY:
                continue
            try:
                loader = LOADER_REGISTRY[source_name]()
            except Exception as exc:  # noqa: BLE001
                logger.debug("buy_point_screen: %s construct failed: %s", source_name, exc)
                continue
            if not loader.is_available():
                continue

            if source_name == "tushare":
                got = _fetch_codes_serial(
                    loader,
                    remaining,
                    start_date=start_date,
                    end_date=end_date,
                    fields=fields,
                    min_interval_sec=_TUSHARE_MIN_INTERVAL_SEC,
                )
            elif source_name == "akshare":
                got = _fetch_codes_parallel(
                    loader,
                    remaining,
                    start_date=start_date,
                    end_date=end_date,
                    fields=fields,
                    retries=_AKSHARE_RETRIES,
                    workers=4,
                )
            else:
                got = _fetch_codes_parallel(
                    loader,
                    remaining,
                    start_date=start_date,
                    end_date=end_date,
                    fields=fields,
                )

        results.update(got)
        if len(results) > before:
            sources_used.append(source_name)
        remaining = [c for c in remaining if c not in results]
        logger.info(
            "buy_point_screen: %s fetched %d (total %d/%d, remaining %d)",
            source_name,
            len(got),
            len(results),
            len(codes),
            len(remaining),
        )
        # Stop once coverage is good enough for a useful screen.
        if len(results) >= max(50, int(len(codes) * 0.8)):
            break
        if remaining and source_name != chain[-1]:
            time.sleep(0.2)

    if not results:
        raise RuntimeError(
            f"Could not fetch OHLCV for any of {len(codes)} tickers "
            f"(market={market}, tried={list(chain)})"
        )

    source = "+".join(sources_used) if sources_used else market
    return results, source


def _fetch_a_share_tushare_bulk(
    codes: list[str],
    *,
    start_date: str,
    end_date: str,
) -> tuple[dict[str, pd.DataFrame], bool]:
    """Pull OHLCV via ``pro.daily(trade_date=…)`` — one call covers all A-shares.

    Skips ``trade_cal`` (often 1 call/hour on free tier) and walks weekdays;
    holiday dates simply return empty frames.

    Returns ``(frames, aborted_on_rate_limit)``.
    """
    import os
    import time

    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token == "your-tushare-token":
        return {}, False

    cache_key = f"{start_date}:{end_date}:{len(codes)}:{hash(tuple(sorted(c.upper() for c in codes)))}"
    now = time.monotonic()
    cached = _bulk_cache.get(cache_key)
    if cached is not None:
        ts, payload = cached
        if now - ts <= _BULK_CACHE_TTL_SEC:
            logger.info("buy_point_screen: tushare_bulk memory cache hit (%d names)", len(payload))
            return {c: payload[c] for c in codes if c in payload}, False

    # Survive uvicorn --reload between screens.
    import pickle

    disk_path = _bulk_disk_dir() / f"bulk_{abs(hash(cache_key))}.pkl"
    if disk_path.exists():
        try:
            saved_at, payload = pickle.loads(disk_path.read_bytes())
            if time.time() - float(saved_at) <= _BULK_CACHE_TTL_SEC:
                _bulk_cache[cache_key] = (time.monotonic(), payload)
                logger.info("buy_point_screen: tushare_bulk disk cache hit (%d names)", len(payload))
                return {c: payload[c] for c in codes if c in payload}, False
        except Exception as exc:  # noqa: BLE001
            logger.debug("buy_point_screen: disk cache read failed: %s", exc)

    try:
        import tushare as ts
    except ImportError:
        return {}, False

    pro = ts.pro_api(token)
    code_set = {c.upper() for c in codes}
    sd = start_date.replace("-", "")
    ed = end_date.replace("-", "")

    # Weekday walk only — avoids burning trade_cal's hourly quota.
    trade_dates: list[str] = []
    cur = datetime.strptime(sd, "%Y%m%d")
    end = datetime.strptime(ed, "%Y%m%d")
    while cur <= end:
        if cur.weekday() < 5:
            trade_dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    buckets: dict[str, list[dict[str, Any]]] = {c: [] for c in code_set}
    last_call = 0.0
    failures = 0
    ok_days = 0
    aborted = False

    for td in trade_dates:
        gap = _TUSHARE_MIN_INTERVAL_SEC - (time.monotonic() - last_call)
        if gap > 0:
            time.sleep(gap)
        last_call = time.monotonic()
        try:
            day = pro.daily(trade_date=td)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            logger.warning("buy_point_screen: daily(%s) failed: %s", td, msg[:160])
            failures += 1
            if "频率超限" in msg or "权限" in msg:
                aborted = True
                break
            if failures >= 5:
                aborted = True
                break
            continue

        failures = 0
        if day is None or day.empty:
            continue
        day = day[day["ts_code"].isin(code_set)]
        if day.empty:
            continue
        ok_days += 1
        for row in day.itertuples(index=False):
            code = str(row.ts_code)
            buckets[code].append(
                {
                    "trade_date": pd.Timestamp(str(row.trade_date)),
                    "open": float(row.open),
                    "high": float(row.high),
                    "low": float(row.low),
                    "close": float(row.close),
                    "volume": float(getattr(row, "vol", 0) or 0),
                }
            )

    results: dict[str, pd.DataFrame] = {}
    min_bars = 40
    for code, rows in buckets.items():
        if len(rows) < min_bars:
            continue
        frame = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
        results[code] = frame

    if results and ok_days >= min_bars:
        cache_payload = {c: df for c, df in results.items()}
        _bulk_cache[cache_key] = (time.monotonic(), cache_payload)
        try:
            disk_path.write_bytes(pickle.dumps((time.time(), cache_payload), protocol=4))
        except Exception as exc:  # noqa: BLE001
            logger.debug("buy_point_screen: disk cache write failed: %s", exc)

    logger.info(
        "buy_point_screen: tushare_bulk ok_days=%d names=%d/%d aborted=%s",
        ok_days,
        len(results),
        len(codes),
        aborted,
    )
    return results, aborted


def _fetch_codes_parallel(
    loader: Any,
    codes: list[str],
    *,
    start_date: str,
    end_date: str,
    fields: list[str] | None = None,
    retries: int = 0,
    workers: int | None = None,
) -> dict[str, pd.DataFrame]:
    import time

    results: dict[str, pd.DataFrame] = {}

    def _one(code: str) -> tuple[str, pd.DataFrame | None]:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                fetched = loader.fetch(
                    codes=[code],
                    start_date=start_date,
                    end_date=end_date,
                    interval="1D",
                    fields=fields,
                )
                frame = fetched.get(code) if isinstance(fetched, dict) else None
                if frame is None or frame.empty:
                    return code, None
                return code, frame
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries:
                    time.sleep(0.4 * (attempt + 1))
        if last_exc is not None:
            logger.debug("buy_point_screen: fetch %s failed: %s", code, last_exc)
        return code, None

    pool_workers = min(workers or _FETCH_WORKERS, max(1, len(codes)))
    with ThreadPoolExecutor(max_workers=pool_workers) as pool:
        futures = [pool.submit(_one, code) for code in codes]
        for fut in as_completed(futures):
            code, frame = fut.result()
            if frame is not None:
                results[code] = frame
    return results


def _fetch_codes_serial(
    loader: Any,
    codes: list[str],
    *,
    start_date: str,
    end_date: str,
    fields: list[str] | None = None,
    min_interval_sec: float,
) -> dict[str, pd.DataFrame]:
    import time

    results: dict[str, pd.DataFrame] = {}
    last_call = 0.0
    for code in codes:
        gap = min_interval_sec - (time.monotonic() - last_call)
        if gap > 0:
            time.sleep(gap)
        last_call = time.monotonic()
        try:
            fetched = loader.fetch(
                codes=[code],
                start_date=start_date,
                end_date=end_date,
                interval="1D",
                fields=fields,
            )
            frame = fetched.get(code) if isinstance(fetched, dict) else None
            if frame is not None and not frame.empty:
                results[code] = frame
        except Exception as exc:  # noqa: BLE001
            logger.debug("buy_point_screen: serial fetch %s failed: %s", code, exc)
            msg = str(exc)
            if "频率超限" in msg or "权限" in msg or "rate" in msg.lower():
                logger.warning("buy_point_screen: aborting tushare fill (%s)", msg[:120])
                break
    return results
