"""Cron expression + preset handling for scheduled tasks.

Preset names are projected onto equivalent 5-field cron expressions so the
scheduler only has to understand one form. :func:`next_run_ms` is the single
entry point the runner uses to advance a job after each fire.

Purity contract: :func:`next_run_ms` and :func:`preset_to_cron` are pure and
take ``now_ms`` explicitly — they never read the wall clock. The only clock
read lives in :func:`next_run_ms_from_now`, a thin wrapper for caller
convenience outside the hot path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Preset → cron mapping
# ---------------------------------------------------------------------------

#: Named preset keys exposed to the UI, mapped to 5-field cron expressions.
#: Times are interpreted in the task's ``timezone``. Add new presets here;
#: the route layer validates against :data:`VALID_PRESETS`.
PRESET_TO_CRON: dict[str, str] = {
    "every_minute": "* * * * *",
    "every_5_minutes": "*/5 * * * *",
    "every_15_minutes": "*/15 * * * *",
    "every_30_minutes": "*/30 * * * *",
    "hourly": "0 * * * *",
    "daily_0000": "0 0 * * *",
    "daily_0930": "30 9 * * *",
    "daily_1500": "0 15 * * *",
    "weekdays_0930": "30 9 * * 1-5",
    "weekdays_1600": "0 16 * * 1-5",
    "weekly_mon_0930": "30 9 * * 1",
    "monthly_1st_0000": "0 0 1 * *",
}

#: Frozen set for O(1) validation in the route / store layer.
VALID_PRESETS: frozenset[str] = frozenset(PRESET_TO_CRON.keys())

#: Human-readable labels for the UI picker. Keep keys in sync with PRESET_TO_CRON.
PRESET_LABELS: dict[str, str] = {
    "every_minute": "Every minute",
    "every_5_minutes": "Every 5 minutes",
    "every_15_minutes": "Every 15 minutes",
    "every_30_minutes": "Every 30 minutes",
    "hourly": "Every hour (at :00)",
    "daily_0000": "Daily at 00:00",
    "daily_0930": "Daily at 09:30",
    "daily_1500": "Daily at 15:00",
    "weekdays_0930": "Weekdays at 09:30",
    "weekdays_1600": "Weekdays at 16:00",
    "weekly_mon_0930": "Every Monday at 09:30",
    "monthly_1st_0000": "Monthly on the 1st at 00:00",
}


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------


def preset_to_cron(preset: str) -> str:
    """Return the 5-field cron expression for a preset key.

    Args:
        preset: A key from :data:`PRESET_TO_CRON`.

    Returns:
        The equivalent cron expression.

    Raises:
        ValueError: If ``preset`` is not a registered preset.
    """
    if preset not in PRESET_TO_CRON:
        raise ValueError(
            f"unknown preset {preset!r}; expected one of {sorted(VALID_PRESETS)}"
        )
    return PRESET_TO_CRON[preset]


def resolve_cron(*, schedule_type: str, preset: str | None, cron_expr: str | None) -> str:
    """Resolve a task's schedule to a canonical cron expression.

    Args:
        schedule_type: ``"preset"`` or ``"cron"``.
        preset: Preset key (required when ``schedule_type == "preset"``).
        cron_expr: Cron expression (required when ``schedule_type == "cron"``).

    Returns:
        A 5-field cron expression.

    Raises:
        ValueError: On unknown schedule type, missing required field, or an
            invalid cron expression.
    """
    if schedule_type == "preset":
        if not preset:
            raise ValueError("schedule_type=preset requires schedule_preset")
        return preset_to_cron(preset)
    if schedule_type == "cron":
        if not cron_expr:
            raise ValueError("schedule_type=cron requires cron_expr")
        # Validate shape via croniter (raises ValueError on bad input).
        _validate_cron(cron_expr)
        return cron_expr.strip()
    raise ValueError(f"unknown schedule_type: {schedule_type!r}")


def _validate_cron(expr: str) -> None:
    """Validate a cron expression by asking croniter to parse it.

    Args:
        expr: A 5-field cron expression.

    Raises:
        ValueError: If the expression is malformed.
    """
    from croniter import croniter  # local import keeps import cost off startup

    # croniter.isValid exists, but parsing is what surfaces the clearest error.
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression: {expr!r}")


def next_run_ms(
    *,
    schedule_type: str,
    preset: str | None,
    cron_expr: str | None,
    timezone_name: str,
    now_ms: int,
) -> int:
    """Return the epoch-ms timestamp of the next scheduled fire after ``now_ms``.

    The schedule is interpreted in ``timezone_name``; the returned timestamp is
    UTC epoch-ms. Deterministic — reads no clock.

    Args:
        schedule_type: ``"preset"`` or ``"cron"``.
        preset: Preset key (when ``schedule_type == "preset"``).
        cron_expr: Cron expression (when ``schedule_type == "cron"``).
        timezone_name: IANA timezone the schedule is expressed in.
        now_ms: Reference time in epoch ms.

    Returns:
        Epoch-ms timestamp of the next fire strictly after ``now_ms``.

    Raises:
        ValueError: On bad schedule spec, unknown timezone, or invalid cron.
    """
    from croniter import croniter  # local import

    cron = resolve_cron(
        schedule_type=schedule_type, preset=preset, cron_expr=cron_expr
    )
    try:
        tz = ZoneInfo(timezone_name)
    except Exception as exc:  # ZoneInfo raises KeyError / ValueError for bad tz
        raise ValueError(f"unknown timezone: {timezone_name!r}") from exc

    # Anchor at the current instant in the target tz so cron fields (hour,
    # weekday, day-of-month) are evaluated in the user's local frame.
    now_local = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).astimezone(tz)
    cron_iter = croniter(cron, now_local, ret_type=datetime)
    next_local = cron_iter.get_next(datetime)
    # Convert back to UTC epoch-ms.
    return int(next_local.astimezone(timezone.utc).timestamp() * 1000)


def next_run_ms_from_now(
    *,
    schedule_type: str,
    preset: str | None,
    cron_expr: str | None,
    timezone_name: str,
) -> int:
    """Convenience wrapper around :func:`next_run_ms` using the current wall clock.

    Args:
        schedule_type: ``"preset"`` or ``"cron"``.
        preset: Preset key (when ``schedule_type == "preset"``).
        cron_expr: Cron expression (when ``schedule_type == "cron"``).
        timezone_name: IANA timezone the schedule is expressed in.

    Returns:
        Epoch-ms timestamp of the next fire strictly after now.
    """
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return next_run_ms(
        schedule_type=schedule_type,
        preset=preset,
        cron_expr=cron_expr,
        timezone_name=timezone_name,
        now_ms=now_ms,
    )


def humanize_schedule(
    *, schedule_type: str, preset: str | None, cron_expr: str | None, timezone_name: str
) -> str:
    """Return a short human-readable description of a schedule for UI badges.

    Args:
        schedule_type: ``"preset"`` or ``"cron"``.
        preset: Preset key (when ``schedule_type == "preset"``).
        cron_expr: Cron expression (when ``schedule_type == "cron"``).
        timezone_name: IANA timezone, included in the description.

    Returns:
        A short label like ``"Daily at 09:30 (Asia/Shanghai)"``.
    """
    if schedule_type == "preset" and preset in PRESET_LABELS:
        return f"{PRESET_LABELS[preset]} ({timezone_name})"
    if schedule_type == "cron" and cron_expr:
        return f"cron `{cron_expr}` ({timezone_name})"
    return f"unknown schedule ({timezone_name})"
