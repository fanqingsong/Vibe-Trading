"""Filesystem layout for the notify subsystem.

Mirrors the live channel convention (``src/live/paths.py``): all runtime state
lives under ``<runtime_root>/notify`` where ``runtime_root`` defaults to
``~/.vibe-trading`` via :func:`src.config.paths.get_runtime_root`.

Layout::

    <runtime_root>/notify/sent_log.jsonl   append-only send ledger
"""

from __future__ import annotations

from pathlib import Path

from src.config.paths import get_runtime_root


def notify_root() -> Path:
    """Return the root directory for all notify-channel state.

    Returns:
        ``<runtime_root>/notify``. The directory is created here (notify writes
        are best-effort and tolerate a missing root on read paths).
    """
    root = get_runtime_root() / "notify"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sent_log_path() -> Path:
    """Return the append-only send ledger path."""
    return notify_root() / "sent_log.jsonl"
