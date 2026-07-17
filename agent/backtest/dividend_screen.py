"""High dividend-yield stock screener.

Screens equities by trailing dividend yield with optional PE / market-cap
filters. Used by the ``/dividends`` API endpoint.

Data sources
------------
- A-shares / ``csi300``: Tushare ``daily_basic`` (``dv_ttm`` in percent),
  with AKShare ``stock_fhps_em`` (现金分红-股息率) as free fallback when
  Tushare is missing or rate-limited.
- US / ``sp500``: yfinance ``info.dividendYield`` (normalized to percent).
- Custom code lists: market inferred per ticker.
"""

from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

Universe = Literal["csi300", "sp500", "custom"]

# Free-tier daily_basic is often 1 call/minute. Sleep between empty→retry walks
# so we do not burn the next slot immediately. Tests set this to 0.
_DAILY_BASIC_RETRY_GAP_SEC = float(os.getenv("VIBE_TUSHARE_DAILY_BASIC_GAP_SEC", "65"))
_CN_TZ = ZoneInfo("Asia/Shanghai")

# Blue-chip A-share fallback when index constituents cannot be loaded.
_CSI300_FALLBACK_CODES = [
    "600519.SH", "601318.SH", "600036.SH", "000333.SZ", "000858.SZ",
    "601166.SH", "600276.SH", "601398.SH", "601288.SH", "600030.SH",
    "600887.SH", "601012.SH", "601888.SH", "000651.SZ", "600028.SH",
    "601628.SH", "600000.SH", "601088.SH", "601857.SH", "600009.SH",
    "601899.SH", "002594.SZ", "600585.SH", "300750.SZ", "601658.SH",
    "600048.SH", "601138.SH", "601668.SH", "000001.SZ", "000002.SZ",
]

_SP500_FALLBACK_CODES = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "BRK-B",
    "JPM", "JNJ", "V", "PG", "UNH", "MA", "HD", "XOM", "LLY", "MRK",
    "PEP", "KO", "ABBV", "AVGO", "CVX", "WMT", "COST", "ADBE", "MCD",
    "CRM", "ACN", "BAC", "TMO", "ORCL", "CSCO", "ABT", "WFC", "DHR",
    "VZ", "PFE", "INTC", "DIS", "CMCSA", "AMD", "TXN", "PM", "QCOM",
    "NEE", "RTX", "HON", "T", "IBM",
]

_US_FETCH_WORKERS = 8


def _tushare_token() -> str | None:
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token == "your-tushare-token":
        return None
    return token


def _infer_a_share(code: str) -> bool:
    code_upper = code.upper()
    if code_upper.endswith((".SH", ".SZ", ".BJ")):
        return True
    if code_upper.endswith(".HK") or code_upper.endswith(".US"):
        return False
    # Bare 6-digit A-share codes
    digits = code_upper.split(".")[0]
    return digits.isdigit() and len(digits) == 6


def _normalize_a_share_code(code: str) -> str:
    """Ensure ``XXXXXX.SH/SZ/BJ`` form."""
    code = code.strip().upper()
    if "." in code:
        return code
    digits = code.zfill(6)
    if digits.startswith(("60", "68", "90")):
        return f"{digits}.SH"
    if digits.startswith(("00", "30")):
        return f"{digits}.SZ"
    if digits.startswith(("8", "4")):
        return f"{digits}.BJ"
    return f"{digits}.SZ"


def _normalize_us_code(code: str) -> str:
    code = code.strip().upper()
    if code.endswith(".US"):
        code = code[:-3]
    return code.replace(".", "-")


def _normalize_yield_pct(raw: Any) -> float | None:
    """Normalize vendor yield to percent (e.g. 3.5 means 3.5%).

    yfinance historically returns a fraction (0.035); newer builds sometimes
    already return percent. Heuristic: values in (0, 1] are treated as fractions.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if pd.isna(value) or value <= 0:
        return None
    if value <= 1.0:
        value *= 100.0
    return round(value, 4)


def resolve_universe_codes(universe: Universe, codes: list[str] | None = None) -> list[str]:
    """Resolve ticker list for a named universe or custom codes."""
    if universe == "custom":
        if not codes:
            raise ValueError("codes required when universe='custom'")
        return [_normalize_a_share_code(c) if _infer_a_share(c) else _normalize_us_code(c) for c in codes]

    if universe == "csi300":
        return _resolve_csi300_codes()

    if universe == "sp500":
        return _resolve_sp500_codes()

    raise ValueError(f"universe must be csi300|sp500|custom, got {universe!r}")


def _resolve_csi300_codes() -> list[str]:
    token = _tushare_token()
    if token:
        try:
            import tushare as ts

            pro = ts.pro_api(token)
            end = datetime.now()
            start = end - timedelta(days=45)
            weights = pro.index_weight(
                index_code="399300.SZ",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            if weights is not None and not weights.empty:
                latest = weights["trade_date"].max()
                codes = (
                    weights[weights["trade_date"] == latest]["con_code"]
                    .drop_duplicates()
                    .tolist()
                )
                if codes:
                    logger.info("dividend_screen: %d CSI300 names from index_weight @ %s", len(codes), latest)
                    return codes
        except Exception as exc:  # noqa: BLE001
            logger.warning("dividend_screen: Tushare index_weight failed (%s)", exc)

    try:
        import akshare as ak

        cons = ak.index_stock_cons_csindex(symbol="000300")
        if cons is not None and not cons.empty and "成分券代码" in cons.columns:
            raw = cons["成分券代码"].astype(str).str.zfill(6).tolist()
            codes = []
            for digits in raw:
                suffix = ".SH" if digits.startswith(("60", "68", "90")) else ".SZ"
                codes.append(f"{digits}{suffix}")
            if codes:
                logger.info("dividend_screen: %d CSI300 names from csindex", len(codes))
                return codes
    except Exception as exc:  # noqa: BLE001
        logger.warning("dividend_screen: AKShare CSI300 constituents failed (%s)", exc)

    logger.warning("dividend_screen: using %d-name CSI300 fallback", len(_CSI300_FALLBACK_CODES))
    return list(_CSI300_FALLBACK_CODES)


def _resolve_sp500_codes() -> list[str]:
    try:
        import io

        import requests

        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Vibe-Trading/0.1 (dividend screen; "
                    "https://github.com/HKUDS/Vibe-Trading)"
                )
            },
            timeout=20,
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        for tbl in tables:
            if "Symbol" in tbl.columns:
                tickers = tbl["Symbol"].astype(str).str.strip().tolist()
                tickers = [_normalize_us_code(t) for t in tickers if t and t != "nan"]
                if tickers:
                    logger.info("dividend_screen: %d SP500 tickers from Wikipedia", len(tickers))
                    return tickers
    except Exception as exc:  # noqa: BLE001
        logger.warning("dividend_screen: SP500 Wikipedia fetch failed (%s)", exc)

    logger.warning("dividend_screen: using %d-name SP500 fallback", len(_SP500_FALLBACK_CODES))
    return list(_SP500_FALLBACK_CODES)


def _is_tushare_hard_error(exc: BaseException) -> bool:
    """True when the error is auth / quota / permission — not worth retrying."""
    msg = str(exc)
    markers = (
        "频率超限",
        "没有接口",
        "访问权限",
        "积分不足",
        "token不对",
        "您的token",
        "权限的具体详情",
    )
    return any(m in msg for m in markers)


def _format_tushare_failure(api: str, exc: BaseException) -> str:
    msg = str(exc).strip() or type(exc).__name__
    if "频率超限" in msg:
        # Prefer the limit Tushare reported (e.g. 1次/分钟) over a hardcoded hint.
        m = re.search(r"频率超限\(([^)]+)\)", msg)
        limit = m.group(1) if m else "quota exhausted"
        return (
            f"Tushare {api} rate-limited ({msg}). "
            f"Wait for the limit window ({limit}) and retry, or upgrade Tushare积分."
        )
    if "没有接口" in msg or "访问权限" in msg or "权限" in msg:
        return (
            f"Tushare {api} permission denied ({msg}). "
            "Your token lacks access to this endpoint; upgrade the Tushare plan."
        )
    if "token" in msg.lower() or "您的token" in msg:
        return (
            f"Tushare token rejected ({msg}). "
            "Update TUSHARE_TOKEN in Settings."
        )
    return f"Tushare {api} failed: {msg}"


def _candidate_trade_dates(pro: Any, trade_date: str | None) -> list[str]:
    """Newest-first trade dates to try for daily_basic.

    When ``trade_date`` is set, only that day is tried. Otherwise prefer open
    sessions from ``trade_cal``; if that call fails (common on free-tier rate
    limits), fall back to walking back calendar days so today's empty/unpublished
    ``daily_basic`` can still resolve to the prior session.
    """
    if trade_date:
        return [trade_date.replace("-", "")]

    cal_end = datetime.now()
    cal_start = cal_end - timedelta(days=21)
    try:
        cal = pro.trade_cal(
            exchange="SSE",
            start_date=cal_start.strftime("%Y%m%d"),
            end_date=cal_end.strftime("%Y%m%d"),
            is_open="1",
        )
        if cal is not None and not cal.empty:
            dates = sorted(
                {str(d) for d in cal["cal_date"].tolist()},
                reverse=True,
            )
            if dates:
                return dates
    except Exception as exc:  # noqa: BLE001
        if _is_tushare_hard_error(exc):
            logger.warning(
                "dividend_screen: trade_cal unavailable (%s); "
                "walking back calendar days",
                exc,
            )
        else:
            logger.warning("dividend_screen: trade_cal failed (%s)", exc)

    return [
        (cal_end - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(0, 14)
    ]


def _defer_today_trade_date(candidates: list[str]) -> list[str]:
    """Put Shanghai 'today' after prior sessions when auto-picking dates.

    ``daily_basic`` for the current session is often empty until evening publish.
    Trying today first then walking back burns free-tier 1/min quota on the
    second call. Prefer the previous open day so the common path is one call.
    """
    if len(candidates) < 2:
        return candidates
    today = datetime.now(_CN_TZ).strftime("%Y%m%d")
    if candidates[0] != today:
        return candidates
    return candidates[1:] + [candidates[0]]


def _stock_name_map(pro: Any, codes: list[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        basic = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
        if basic is not None and not basic.empty:
            wanted = set(codes)
            for _, row in basic.iterrows():
                code = row["ts_code"]
                if code in wanted:
                    names[code] = str(row["name"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("dividend_screen: stock_basic failed (%s)", exc)
    return names


def _digits_to_ts_code(digits: str) -> str:
    digits = str(digits).strip().zfill(6)
    suffix = ".SH" if digits.startswith(("60", "68", "90")) else ".SZ"
    return f"{digits}{suffix}"


def _fhps_report_dates() -> list[str]:
    """Report periods for stock_fhps_em: year-ends first, then mid-years.

    Mid-year periods are often sparse early in the season (a handful of
    interim dividends). Preferring ``*1231`` avoids returning a near-empty
    screen when a thin ``*0630`` happens to match one universe name.
    """
    now = datetime.now(_CN_TZ)
    today = now.strftime("%Y%m%d")
    year_ends: list[str] = []
    mid_years: list[str] = []
    for y in range(now.year, now.year - 3, -1):
        ye, my = f"{y}1231", f"{y}0630"
        if ye <= today:
            year_ends.append(ye)
        if my <= today:
            mid_years.append(my)
    return year_ends + mid_years


def _fhps_min_coverage(universe_size: int) -> int:
    """Minimum overlapping names before accepting an fhps period as primary."""
    if universe_size <= 5:
        return 1
    return max(10, universe_size // 10)


def _fetch_a_share_basics_akshare(codes: list[str]) -> tuple[pd.DataFrame, str, str]:
    """Fetch A-share dividend yields via AKShare East Money 分红送配.

    Walks year-end then mid-year report periods, keeping the newest yield per
    code. Skips thin early-season mid-year dumps that would otherwise return
    before a full annual file is tried. PE / PB / market-cap are not available
    from this endpoint (optional filters simply no-op).
    """
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "akshare is required for the free A-share dividend-yield fallback"
        ) from exc

    wanted = set(codes)
    by_code: dict[str, dict[str, Any]] = {}
    dates_used: list[str] = []
    last_error: BaseException | None = None
    min_coverage = _fhps_min_coverage(len(wanted))
    report_dates = _fhps_report_dates()

    for idx, report_date in enumerate(report_dates):
        try:
            bulk = ak.stock_fhps_em(date=report_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dividend_screen: stock_fhps_em(%s) failed (%s)",
                report_date,
                exc,
            )
            last_error = exc
            continue

        if bulk is None or bulk.empty or "代码" not in bulk.columns:
            continue
        if "现金分红-股息率" not in bulk.columns:
            continue

        added_codes: list[str] = []
        for _, row in bulk.iterrows():
            ts_code = _digits_to_ts_code(row["代码"])
            if ts_code not in wanted or ts_code in by_code:
                continue
            yield_pct = _normalize_yield_pct(row.get("现金分红-股息率"))
            if yield_pct is None:
                continue
            by_code[ts_code] = {
                "ts_code": ts_code,
                "name": str(row.get("名称") or ""),
                "dv_ttm": yield_pct,
                "pe_ttm": None,
                "pb": None,
                "total_mv": None,
                "close": None,
            }
            added_codes.append(ts_code)

        added = len(added_codes)
        later_annual = any(d.endswith("1231") for d in report_dates[idx + 1 :])
        # Ignore sparse mid-year dumps when a fuller year-end is still ahead.
        if (
            added
            and added < min_coverage
            and report_date.endswith("0630")
            and later_annual
        ):
            for code in added_codes:
                by_code.pop(code, None)
            logger.info(
                "dividend_screen: skip thin stock_fhps_em(%s) (%d names); "
                "prefer later year-end",
                report_date,
                added,
            )
            continue

        if added:
            dates_used.append(report_date)
            logger.info(
                "dividend_screen: stock_fhps_em(%s) added %d (total %d/%d)",
                report_date,
                added,
                len(by_code),
                len(wanted),
            )

        # Stop once a year-end (or any period) gives solid universe coverage.
        if len(by_code) >= min_coverage and (
            report_date.endswith("1231")
            or len(by_code) >= max(min_coverage, len(wanted) // 2)
        ):
            break

    if not by_code:
        if last_error is not None:
            raise RuntimeError(
                f"AKShare stock_fhps_em failed: {last_error}"
            ) from last_error
        raise RuntimeError(
            "No AKShare dividend-yield rows for the requested A-share universe. "
            "Tried recent year-end/mid-year report periods via stock_fhps_em."
        )

    primary = next((d for d in dates_used if d.endswith("1231")), dates_used[0])
    source = f"akshare.stock_fhps_em(date={primary})"
    if len(dates_used) > 1:
        source = f"akshare.stock_fhps_em(date={primary}+{len(dates_used) - 1}more)"
    frame = pd.DataFrame(list(by_code.values()))
    logger.info(
        "dividend_screen: %d A-share yields from %s",
        len(frame),
        source,
    )
    return frame, primary, source


def _fetch_a_share_basics_tushare(
    codes: list[str],
    trade_date: str | None = None,
) -> tuple[pd.DataFrame, str, str]:
    """Fetch latest daily_basic rows via Tushare."""
    token = _tushare_token()
    if not token:
        raise RuntimeError(
            "TUSHARE_TOKEN is required to screen A-share dividend yields "
            "(daily_basic.dv_ttm). Configure it in Settings or the environment."
        )

    import tushare as ts

    pro = ts.pro_api(token)
    candidates = _candidate_trade_dates(pro, trade_date)
    if not trade_date:
        candidates = _defer_today_trade_date(candidates)
    fields = "ts_code,trade_date,close,dv_ttm,pe_ttm,pb,total_mv"

    frame: pd.DataFrame | None = None
    td = candidates[0]
    source = "tushare.daily_basic(trade_date)"
    last_hard_error: BaseException | None = None

    # Prefer a published prior session first (see _defer_today_trade_date). If a
    # day is empty, wait before the next daily_basic call — free tier is often
    # 1/min and an immediate retry would rate-limit.
    for idx, candidate in enumerate(candidates):
        try:
            bulk = pro.daily_basic(trade_date=candidate, fields=fields)
        except Exception as exc:  # noqa: BLE001
            if _is_tushare_hard_error(exc):
                # Do not hammer per-code fallback on rate-limit / permission errors.
                raise RuntimeError(_format_tushare_failure("daily_basic", exc)) from exc
            logger.warning(
                "dividend_screen: daily_basic(%s) failed (%s); trying earlier date",
                candidate,
                exc,
            )
            last_hard_error = exc
            continue

        if bulk is not None and not bulk.empty:
            frame = bulk
            td = candidate
            source = "tushare.daily_basic(trade_date)"
            break
        logger.info(
            "dividend_screen: daily_basic empty for %s; trying earlier date",
            candidate,
        )
        if idx + 1 < len(candidates) and _DAILY_BASIC_RETRY_GAP_SEC > 0:
            logger.info(
                "dividend_screen: waiting %.0fs before next daily_basic "
                "(avoid free-tier rate limit)",
                _DAILY_BASIC_RETRY_GAP_SEC,
            )
            time.sleep(_DAILY_BASIC_RETRY_GAP_SEC)

    if frame is None or frame.empty:
        if last_hard_error is not None:
            raise RuntimeError(
                _format_tushare_failure("daily_basic", last_hard_error)
            ) from last_hard_error
        tried = ", ".join(candidates[:5])
        raise RuntimeError(
            f"No daily_basic rows for trade dates [{tried}]. "
            "Check Tushare积分 / network, or pass an earlier trade_date."
        )

    frame = frame[frame["ts_code"].isin(codes)].copy()
    if frame.empty:
        raise RuntimeError(
            f"Universe codes not found in daily_basic for trade_date={td}"
        )

    for col in ("close", "dv_ttm", "pe_ttm", "pb", "total_mv"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    names = _stock_name_map(pro, codes)
    frame["name"] = frame["ts_code"].map(names).fillna("")
    return frame, td, source


def _fetch_a_share_basics(
    codes: list[str],
    trade_date: str | None = None,
) -> tuple[pd.DataFrame, str, str]:
    """Fetch A-share dividend fundamentals (Tushare, else AKShare).

    Returns (dataframe, trade_date_or_report_date, source_label).
    """
    token = _tushare_token()
    tushare_error: BaseException | None = None

    if token:
        try:
            return _fetch_a_share_basics_tushare(codes, trade_date=trade_date)
        except RuntimeError as exc:
            tushare_error = exc
            logger.warning(
                "dividend_screen: Tushare unavailable (%s); falling back to AKShare",
                exc,
            )
    else:
        logger.info(
            "dividend_screen: no TUSHARE_TOKEN; using AKShare stock_fhps_em"
        )

    try:
        return _fetch_a_share_basics_akshare(codes)
    except Exception as ak_exc:  # noqa: BLE001
        if tushare_error is not None:
            raise RuntimeError(
                f"{tushare_error} AKShare fallback also failed: {ak_exc}"
            ) from ak_exc
        raise


def _fetch_us_basics(codes: list[str]) -> tuple[pd.DataFrame, str, str]:
    """Fetch dividend yield snapshots for US tickers via yfinance."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required to screen US dividend yields") from exc

    def _one(code: str) -> dict[str, Any] | None:
        try:
            info = yf.Ticker(code).info or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("dividend_screen: yfinance %s failed: %s", code, exc)
            return None
        yield_pct = _normalize_yield_pct(
            info.get("dividendYield")
            or info.get("trailingAnnualDividendYield")
            or info.get("yield")
        )
        pe = info.get("trailingPE") or info.get("forwardPE")
        pb = info.get("priceToBook")
        mv = info.get("marketCap")
        close = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        name = info.get("shortName") or info.get("longName") or ""
        try:
            pe_f = float(pe) if pe is not None else None
        except (TypeError, ValueError):
            pe_f = None
        try:
            pb_f = float(pb) if pb is not None else None
        except (TypeError, ValueError):
            pb_f = None
        try:
            mv_f = float(mv) if mv is not None else None
        except (TypeError, ValueError):
            mv_f = None
        try:
            close_f = float(close) if close is not None else None
        except (TypeError, ValueError):
            close_f = None
        return {
            "ts_code": code,
            "name": name,
            "dv_ttm": yield_pct,
            "pe_ttm": pe_f,
            "pb": pb_f,
            # Store USD market cap; convert to "亿元-equivalent" display later via unit flag.
            "total_mv": mv_f,
            "close": close_f,
        }

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=_US_FETCH_WORKERS) as pool:
        futures = {pool.submit(_one, c): c for c in codes}
        for fut in as_completed(futures):
            row = fut.result()
            if row is not None:
                rows.append(row)

    if not rows:
        raise RuntimeError("No yfinance fundamentals returned for the requested US universe")

    frame = pd.DataFrame(rows)
    td = datetime.now().strftime("%Y%m%d")
    return frame, td, "yfinance.info"


def apply_dividend_filters(
    frame: pd.DataFrame,
    *,
    min_yield: float,
    max_yield: float | None = None,
    min_market_cap: float | None = None,
    max_pe: float | None = None,
    market_cap_is_wan: bool = True,
) -> pd.DataFrame:
    """Filter and rank a fundamentals frame by dividend yield.

    Args:
        frame: Must contain ``ts_code``, ``dv_ttm``; optional pe/pb/mv/close/name.
        min_yield: Minimum dividend yield in percent.
        max_yield: Optional cap to reduce extreme yield traps.
        min_market_cap: Minimum market cap. For A-shares this is **亿元**
            (Tushare ``total_mv`` is 万元, so threshold * 10000). For US this
            is **USD** absolute when ``market_cap_is_wan`` is False.
        max_pe: Optional maximum trailing PE (positive only).
        market_cap_is_wan: True when ``total_mv`` is Tushare 万元 units.
    """
    if frame.empty:
        return frame

    out = frame.copy()
    out["dv_ttm"] = pd.to_numeric(out["dv_ttm"], errors="coerce")
    out = out[out["dv_ttm"].notna() & (out["dv_ttm"] >= float(min_yield))]
    if max_yield is not None:
        out = out[out["dv_ttm"] <= float(max_yield)]

    if max_pe is not None and "pe_ttm" in out.columns:
        pe = pd.to_numeric(out["pe_ttm"], errors="coerce")
        out = out[pe.notna() & (pe > 0) & (pe <= float(max_pe))]

    if min_market_cap is not None and "total_mv" in out.columns:
        mv = pd.to_numeric(out["total_mv"], errors="coerce")
        if market_cap_is_wan:
            # User-facing threshold is 亿元; Tushare total_mv is 万元.
            threshold = float(min_market_cap) * 10_000.0
        else:
            threshold = float(min_market_cap)
        out = out[mv.notna() & (mv >= threshold)]

    return out.sort_values("dv_ttm", ascending=False)


def _rows_from_frame(
    frame: pd.DataFrame,
    *,
    market_cap_unit: str,
    top: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for _, row in frame.head(top).iterrows():
        mv_raw = row.get("total_mv")
        market_cap: float | None
        if mv_raw is None or (isinstance(mv_raw, float) and pd.isna(mv_raw)):
            market_cap = None
        else:
            mv_f = float(mv_raw)
            if market_cap_unit == "CNY_yi":
                market_cap = round(mv_f / 10_000.0, 2)  # 万元 → 亿元
            else:
                market_cap = round(mv_f, 0)

        pe_raw = row.get("pe_ttm")
        pb_raw = row.get("pb")
        close_raw = row.get("close")

        def _opt_float(v: Any, ndigits: int = 2) -> float | None:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            try:
                return round(float(v), ndigits)
            except (TypeError, ValueError):
                return None

        results.append(
            {
                "code": str(row["ts_code"]),
                "name": str(row.get("name") or ""),
                "dividend_yield": round(float(row["dv_ttm"]), 2),
                "pe": _opt_float(pe_raw),
                "pb": _opt_float(pb_raw),
                "market_cap": market_cap,
                "close": _opt_float(close_raw),
            }
        )
    return results


def screen_high_dividend(
    *,
    universe: Universe = "csi300",
    codes: list[str] | None = None,
    min_yield: float = 3.0,
    max_yield: float | None = None,
    min_market_cap: float | None = None,
    max_pe: float | None = None,
    top: int = 50,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Screen a universe for high dividend-yield stocks.

    Returns a JSON-serializable dict with ranked results.
    """
    if min_yield < 0:
        raise ValueError("min_yield must be >= 0")
    if max_yield is not None and max_yield < min_yield:
        raise ValueError("max_yield must be >= min_yield")
    if top < 1 or top > 500:
        raise ValueError("top must be between 1 and 500")

    resolved = resolve_universe_codes(universe, codes)
    if not resolved:
        raise ValueError("Resolved universe is empty")

    # Decide market from universe (custom: all codes must share one market).
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
        frame, td, source = _fetch_a_share_basics(resolved, trade_date=trade_date)
        filtered = apply_dividend_filters(
            frame,
            min_yield=min_yield,
            max_yield=max_yield,
            min_market_cap=min_market_cap,
            max_pe=max_pe,
            market_cap_is_wan=True,
        )
        market_cap_unit = "CNY_yi"
        market = "a_share"
    else:
        us_codes = [_normalize_us_code(c) for c in resolved]
        frame, td, source = _fetch_us_basics(us_codes)
        # US min_market_cap is interpreted as USD absolute (e.g. 1e10 for $10B).
        filtered = apply_dividend_filters(
            frame,
            min_yield=min_yield,
            max_yield=max_yield,
            min_market_cap=min_market_cap,
            max_pe=max_pe,
            market_cap_is_wan=False,
        )
        market_cap_unit = "USD"
        market = "us_equity"

    results = _rows_from_frame(filtered, market_cap_unit=market_cap_unit, top=top)
    return {
        "universe": universe,
        "market": market,
        "trade_date": td,
        "min_yield": min_yield,
        "max_yield": max_yield,
        "min_market_cap": min_market_cap,
        "max_pe": max_pe,
        "market_cap_unit": market_cap_unit,
        "universe_size": len(resolved),
        "matched": int(len(filtered)),
        "count": len(results),
        "source": source,
        "results": results,
    }
