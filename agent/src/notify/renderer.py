"""Jinja2 rendering for email bodies.

Mirrors the ``shadow_account/reporter.py`` templating pattern: a
``FileSystemLoader`` over ``templates/`` with HTML autoescape, trim/lstrip for
readable template source. CSS is inlined per-template (email clients strip
``<style>`` / external stylesheets), so each template carries its own
``<style>`` block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Registered template names. ``base.html`` is a shared layout; the others are
# concrete templates rendered for specific event families.
_TEMPLATE_NAMES: frozenset[str] = frozenset(
    {
        "trade_alert",
        "report",
        "system",
        "scheduled_report",
        "dividend_screen",
        "screen_table",
    }
)


def _env() -> Environment:
    """Build a Jinja2 environment over the email templates directory."""
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(name: str, **context: Any) -> str:
    """Render a named email template to an HTML string.

    Args:
        name: Template key without extension — one of ``"trade_alert"``,
            ``"report"``, ``"system"``, ``"scheduled_report"``,
            ``"dividend_screen"``, ``"screen_table"``.
        **context: Variables forwarded to the template.

    Returns:
        The rendered HTML.

    Raises:
        ValueError: If ``name`` is not a registered template.
    """
    if name not in _TEMPLATE_NAMES:
        raise ValueError(f"unknown email template: {name!r}")
    return _env().get_template(f"{name}.html").render(**context)


def available_templates() -> tuple[str, ...]:
    """Return the registered template names (sorted, stable for tests)."""
    return tuple(sorted(_TEMPLATE_NAMES))
