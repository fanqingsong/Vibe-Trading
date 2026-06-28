"""Unit tests for the generic scheduler cron helpers (src.scheduler.cron)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.scheduler.cron import (
    PRESET_TO_CRON,
    VALID_PRESETS,
    humanize_schedule,
    next_run_ms,
    preset_to_cron,
    resolve_cron,
)


def _to_utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# preset_to_cron / resolve_cron
# --------------------------------------------------------------------------- #


def test_preset_to_cron_returns_expected_expression() -> None:
    assert preset_to_cron("daily_0930") == "30 9 * * *"
    assert preset_to_cron("hourly") == "0 * * * *"
    assert preset_to_cron("every_5_minutes") == "*/5 * * * *"


def test_preset_to_cron_rejects_unknown_preset() -> None:
    with pytest.raises(ValueError, match="unknown preset"):
        preset_to_cron("nonsense")


def test_resolve_cron_preset_branch() -> None:
    assert resolve_cron(schedule_type="preset", preset="weekdays_0930", cron_expr=None) == "30 9 * * 1-5"


def test_resolve_cron_cron_branch_validates_shape() -> None:
    assert resolve_cron(schedule_type="cron", preset=None, cron_expr="0 */4 * * *") == "0 */4 * * *"


def test_resolve_cron_rejects_bad_cron() -> None:
    with pytest.raises(ValueError, match="invalid cron expression"):
        resolve_cron(schedule_type="cron", preset=None, cron_expr="not a cron")


def test_resolve_cron_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="requires schedule_preset"):
        resolve_cron(schedule_type="preset", preset=None, cron_expr=None)
    with pytest.raises(ValueError, match="requires cron_expr"):
        resolve_cron(schedule_type="cron", preset=None, cron_expr=None)


def test_resolve_cron_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown schedule_type"):
        resolve_cron(schedule_type="weekly", preset=None, cron_expr=None)


def test_every_preset_resolves_to_valid_cron() -> None:
    """Every advertised preset must resolve to a parseable cron expression."""
    for key in VALID_PRESETS:
        cron = preset_to_cron(key)
        # resolve_cron would raise if invalid; no assertion needed beyond the call.
        resolve_cron(schedule_type="cron", preset=None, cron_expr=cron)


# --------------------------------------------------------------------------- #
# next_run_ms
# --------------------------------------------------------------------------- #


def test_next_run_ms_daily_preset_in_local_tz() -> None:
    """daily_0930 in Asia/Shanghai fires at 01:30 UTC the next day."""
    # 2026-06-28 03:30 UTC = 11:30 CST → next fire is tomorrow 09:30 CST.
    now_ms = int(datetime(2026, 6, 28, 3, 30, tzinfo=timezone.utc).timestamp() * 1000)
    n = next_run_ms(
        schedule_type="preset",
        preset="daily_0930",
        cron_expr=None,
        timezone_name="Asia/Shanghai",
        now_ms=now_ms,
    )
    # Expected: 2026-06-29 01:30:00 UTC.
    expected = int(datetime(2026, 6, 29, 1, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert n == expected, f"got {_to_utc_iso(n)}"


def test_next_run_ms_every_5_minutes_aligns_to_boundary() -> None:
    """every_5_minutes snaps to the next 5-minute boundary."""
    now_ms = int(datetime(2026, 6, 28, 3, 32, tzinfo=timezone.utc).timestamp() * 1000)
    n = next_run_ms(
        schedule_type="preset",
        preset="every_5_minutes",
        cron_expr=None,
        timezone_name="UTC",
        now_ms=now_ms,
    )
    expected = int(datetime(2026, 6, 28, 3, 35, tzinfo=timezone.utc).timestamp() * 1000)
    assert n == expected


def test_next_run_ms_rejects_unknown_timezone() -> None:
    now_ms = int(datetime(2026, 6, 28, tzinfo=timezone.utc).timestamp() * 1000)
    with pytest.raises(ValueError, match="unknown timezone"):
        next_run_ms(
            schedule_type="preset",
            preset="daily_0930",
            cron_expr=None,
            timezone_name="Mars/Olympus",
            now_ms=now_ms,
        )


def test_next_run_ms_cron_expression_respected() -> None:
    """An explicit cron expression wins regardless of preset/cron field duality."""
    now_ms = int(datetime(2026, 6, 28, 3, 30, tzinfo=timezone.utc).timestamp() * 1000)
    n = next_run_ms(
        schedule_type="cron",
        preset=None,
        cron_expr="0 0 * * *",  # daily at 00:00 UTC
        timezone_name="UTC",
        now_ms=now_ms,
    )
    expected = int(datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert n == expected


# --------------------------------------------------------------------------- #
# humanize_schedule
# --------------------------------------------------------------------------- #


def test_humanize_schedule_preset() -> None:
    s = humanize_schedule(
        schedule_type="preset", preset="daily_0930", cron_expr=None, timezone_name="Asia/Shanghai"
    )
    assert "Daily at 09:30" in s
    assert "Asia/Shanghai" in s


def test_humanize_schedule_cron() -> None:
    s = humanize_schedule(
        schedule_type="cron", preset=None, cron_expr="*/5 * * * *", timezone_name="UTC"
    )
    assert "*/5 * * * *" in s
    assert "UTC" in s


# --------------------------------------------------------------------------- #
# Consistency: every preset must have a human label
# --------------------------------------------------------------------------- #


def test_every_preset_has_a_human_label() -> None:
    from src.scheduler.cron import PRESET_LABELS

    for key in PRESET_TO_CRON:
        assert key in PRESET_LABELS, f"preset {key!r} has no label"
