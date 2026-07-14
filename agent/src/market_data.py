"""Shared market data helpers for MCP and local agent tools."""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 250

# AgentLoop truncates tool results at TOOL_RESULT_LIMIT (10_000 chars) from the
# *front*. Pretty-printed OHLCV JSON for a multi-month range exceeds that, so the
# model only sees April–June and reports "July data missing". Stay under budget
# by emitting compact JSON and dropping the oldest bars when needed.
AGENT_TOOL_RESULT_CHAR_BUDGET = 9_500

_SOURCE_PATTERNS = [
    (re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.I), "tushare"),
    (re.compile(r"^[A-Z]+\.US$", re.I), "yfinance"),
    (re.compile(r"^\d{3,5}\.HK$", re.I), "yfinance"),
    (re.compile(r"^[A-Z]+-USDT$", re.I), "okx"),
    (re.compile(r"^[A-Z]+/USDT$", re.I), "ccxt"),
]


def detect_source(code: str) -> str:
    """Infer the best loader source for a normalized symbol."""
    for pattern, source in _SOURCE_PATTERNS:
        if pattern.match(code):
            return source
    return "tushare"


def get_loader(source: str):
    """Get loader class via registry with fallback support."""
    from backtest.loaders.registry import get_loader_cls_with_fallback

    return get_loader_cls_with_fallback(source)


def _fetch_with_fallback(
    *,
    source: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    interval: str,
    loader_resolver: Callable[[str], type],
) -> dict[str, Any]:
    """Fetch via *source*, walking the market fallback chain for any codes
    that come back empty (e.g. a token that passes ``is_available()`` but
    lacks per-interface permissions, a transient API error, or a symbol the
    primary source simply has no data for).

    Returns a mapping of resolved ``{symbol: records_list}`` — *not* capped,
    *not* JSON-safe'd; the caller post-processes the result. Unresolved codes
    are simply absent from the dict so the caller can detect them.
    """
    from backtest.loaders.registry import FALLBACK_CHAINS, LOADER_REGISTRY

    resolved: dict[str, Any] = {}
    pending = list(codes)
    tried_sources: set[str] = {source}

    # Primary attempt.
    pending = _try_fetch(
        loader_cls=loader_resolver(source),
        codes=pending,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
        out=resolved,
    )
    if not pending:
        return resolved

    # Fallback: walk each market the primary loader advertises and try the
    # next available source for unresolved codes. This catches the case where
    # the loader constructed fine (is_available()=True) but fetch() returned
    # empty due to permission/rate-limit/transient errors.
    #
    # We can't use registry.resolve_loader() here because it returns the first
    # is_available() source — which is often the source that just failed at
    # fetch() time (e.g. Tushare token is present but lacks permissions).
    # Instead we walk FALLBACK_CHAINS ourselves, skipping anything already tried.
    loader_cls = loader_resolver(source)
    markets = getattr(loader_cls, "markets", set()) or set()
    # Prefer specific equity markets before broad ones (mirrors the priority
    # in registry.get_loader_cls_with_fallback).
    _market_priority = {"a_share": 0, "us_equity": 0, "hk_equity": 0, "crypto": 0}
    for market in sorted(markets, key=lambda m: _market_priority.get(m, 9)):
        if not pending:
            break
        for name in FALLBACK_CHAINS.get(market, []):
            if not pending:
                break
            if name in tried_sources or name not in LOADER_REGISTRY:
                continue
            fallback_cls = LOADER_REGISTRY[name]
            # Skip loaders that can't construct or report unavailable; the
            # fetch-time failure we're recovering from is NOT visible here.
            try:
                probe = fallback_cls()
            except Exception:
                continue
            if not probe.is_available():
                continue
            tried_sources.add(name)
            pending = _try_fetch(
                loader_cls=fallback_cls,
                codes=pending,
                start_date=start_date,
                end_date=end_date,
                interval=interval,
                out=resolved,
                log_as=name,
            )

    return resolved


def _try_fetch(
    *,
    loader_cls: type,
    codes: list[str],
    start_date: str,
    end_date: str,
    interval: str,
    out: dict[str, Any],
    log_as: str | None = None,
) -> list[str]:
    """Run one loader's ``fetch`` for *codes*, merge dataframes into *out*
    (keyed by symbol), and return the still-unresolved codes.

    A fetch that raises or returns no data for a code is non-fatal: the code
    stays in the returned pending list so the caller can try a fallback.
    """
    label = log_as or getattr(loader_cls, "name", "loader")
    try:
        loader = loader_cls()
    except Exception:
        logger.debug("loader %s failed to construct", label, exc_info=True)
        return list(codes)
    try:
        data_map = loader.fetch(codes, start_date, end_date, interval=interval)
    except Exception:
        # ERROR level (not warning) so operators notice flaky/broken loaders
        # even when a fallback later saves the request — matches the contract
        # enforced by test_swallowed_loader_exception_is_logged.
        logger.exception(
            "market-data loader %r failed for %s; trying fallback",
            label, codes,
        )
        return list(codes)
    if not data_map:
        return list(codes)
    still_missing: list[str] = []
    for code in codes:
        df = data_map.get(code)
        if df is None or getattr(df, "empty", True):
            still_missing.append(code)
        else:
            out[code] = df
    if still_missing:
        logger.info(
            "market-data loader %r resolved %d/%d codes; %d unresolved -> fallback",
            label, len(codes) - len(still_missing), len(codes), len(still_missing),
        )
    return still_missing


def cap_rows(records: list, max_rows: int) -> list | dict[str, object]:
    """Bound a per-symbol row list to keep tool payloads within budget."""
    n = len(records)
    if max_rows < 0:
        max_rows = DEFAULT_MAX_ROWS
    if max_rows == 0 or n <= max_rows:
        return records
    step = math.ceil(n / max_rows)
    sampled = records[::step]
    if sampled[-1] is not records[-1]:
        sampled = sampled + [records[-1]]
    return {
        "rows": n,
        "returned": len(sampled),
        "truncated": True,
        "policy": f"every-{step}th-row (even stride; last bar pinned)",
        "hint": "narrow the date range, coarsen interval, or set max_rows=0 for all rows",
        "data": sampled,
    }


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def fetch_market_data(
    *,
    codes: list[str],
    start_date: str,
    end_date: str,
    source: str = "auto",
    interval: str = "1D",
    max_rows: int = DEFAULT_MAX_ROWS,
    loader_resolver: Callable[[str], type] = get_loader,
) -> dict[str, Any]:
    """Fetch normalized OHLCV data through the repository loader layer."""
    results: dict[str, Any] = {}

    if source == "auto":
        groups: dict[str, list[str]] = {}
        for code in codes:
            src = detect_source(code)
            groups.setdefault(src, []).append(code)
    else:
        groups = {source: list(codes)}

    for src, src_codes in groups.items():
        data_map = _fetch_with_fallback(
            source=src,
            codes=src_codes,
            start_date=start_date,
            end_date=end_date,
            interval=interval,
            loader_resolver=loader_resolver,
        )
        for symbol, df in data_map.items():
            records = df.reset_index().to_dict(orient="records")
            for row in records:
                for key, value in row.items():
                    row[key] = _json_safe(value)
            results[symbol] = cap_rows(records, max_rows)

    unresolved = [code for code in codes if code not in results]
    if unresolved:
        results["_unresolved"] = unresolved

    return results


def _symbol_bar_list(value: Any) -> list | None:
    """Return the mutable bar list for a symbol payload, if any."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        return value["data"]
    return None


def fit_market_data_payload(
    payload: dict[str, Any],
    *,
    max_chars: int = AGENT_TOOL_RESULT_CHAR_BUDGET,
) -> dict[str, Any]:
    """Shrink *payload* so its compact JSON fits within *max_chars*.

    Drops the oldest bars first (keeps the most recent) — for trading questions
    the recent window matters more than early history. Adds ``_truncated`` when
    any bars are dropped.
    """
    if max_chars <= 0:
        return payload

    # Shallow-copy symbols but clone bar lists so trimming never mutates caller state.
    fitted: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            fitted[key] = list(value)
        elif isinstance(value, dict) and isinstance(value.get("data"), list):
            cloned = dict(value)
            cloned["data"] = list(value["data"])
            fitted[key] = cloned
        else:
            fitted[key] = value
    dropped: dict[str, int] = {}

    def _dump_len(obj: dict[str, Any]) -> int:
        return len(json.dumps(obj, ensure_ascii=False, allow_nan=False))

    if _dump_len(fitted) <= max_chars:
        return fitted

    # Already over budget — reserve room for the ``_truncated`` metadata we append.
    meta_stub = {
        "_truncated": {
            "reason": "agent_tool_char_budget",
            "max_chars": max_chars,
            "policy": "kept_most_recent_bars",
            "dropped_bars": {"X" * 16: 9999},
            "hint": "narrow start_date/end_date or set a smaller max_rows for full detail",
        }
    }
    meta_slack = _dump_len(meta_stub)

    while _dump_len(fitted) + meta_slack > max_chars:
        candidates: list[tuple[str, list]] = []
        for key, value in fitted.items():
            if key.startswith("_"):
                continue
            bars = _symbol_bar_list(value)
            if bars and len(bars) > 1:
                candidates.append((key, bars))
        if not candidates:
            break
        key, bars = max(candidates, key=lambda item: len(item[1]))
        bars.pop(0)
        dropped[key] = dropped.get(key, 0) + 1
        value = fitted[key]
        if isinstance(value, dict) and "data" in value:
            value["returned"] = len(bars)
            value["char_budget_trimmed"] = True

    if dropped:
        fitted["_truncated"] = {
            "reason": "agent_tool_char_budget",
            "max_chars": max_chars,
            "policy": "kept_most_recent_bars",
            "dropped_bars": dropped,
            "hint": "narrow start_date/end_date or set a smaller max_rows for full detail",
        }
    return fitted


def fetch_market_data_json(**kwargs: Any) -> str:
    """Fetch market data and return strict compact JSON under the tool budget.

    Compact (no indent) so multi-month OHLCV fits the agent tool-result limit.
    When still too large, oldest bars are dropped so recent dates (e.g. July)
    remain visible to the model.
    """
    max_chars = kwargs.pop("max_chars", AGENT_TOOL_RESULT_CHAR_BUDGET)
    payload = fetch_market_data(**kwargs)
    fitted = fit_market_data_payload(payload, max_chars=max_chars)
    return json.dumps(fitted, ensure_ascii=False, allow_nan=False)
