"""Email / SMTP configuration resolved from environment and ``agent/.env``.

Reads follow the same convention as the LLM / data-source settings in
``api_server.py``: values come from the process environment (populated at
startup from ``agent/.env``). Secrets are masked when surfaced back to the API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

# Placeholder values for ``SMTP_PASSWORD`` that mean "not really configured".
# Mirrors ``LLM_API_KEY_PLACEHOLDERS`` / ``TUSHARE_TOKEN_PLACEHOLDERS`` in api_server.
SMTP_PASSWORD_PLACEHOLDERS: frozenset[str] = frozenset(
    {"", "your-smtp-password", "your-password", "xxx", "changeme"}
)

# Mask returned by the API for any configured secret. Never the real value.
SMTP_SECRET_MASK: str = "***"


def _is_configured_secret(value: str, placeholders: frozenset[str] = SMTP_PASSWORD_PLACEHOLDERS) -> bool:
    """Return True when a secret is set and not a documented placeholder.

    Local mirror of ``api_server._is_configured_secret`` so the notify module
    stays decoupled from the FastAPI layer (importable from CLI / tests too).
    """
    normalized = value.strip().strip('"').strip("'")
    if not normalized:
        return False
    return normalized.lower() not in {placeholder.lower() for placeholder in placeholders}


@dataclass(frozen=True)
class EmailConfig:
    """Resolved SMTP + notification settings.

    Attributes:
        host: SMTP server host (e.g. ``smtp.qq.com``).
        port: SMTP server port. ``0`` means unset.
        user: SMTP auth username.
        password: SMTP auth password (plaintext in memory; never serialized).
        from_addr: envelope ``From`` address. Defaults to ``user`` when unset.
        use_tls: whether to use TLS (``True`` → implicit TLS / SMTPS on connect,
            ``False`` → STARTTLS upgrade after connect). Port 465 implies TLS;
            ports 25 / 587 imply STARTTLS.
        recipients: default ``To`` list for notifications.
        notify_trade_alerts: enable trade-action / order-fill emails.
        notify_reports: enable report emails.
    """

    host: str = ""
    port: int = 0
    user: str = ""
    password: str = ""
    from_addr: str = ""
    use_tls: bool = True
    recipients: tuple[str, ...] = ()
    notify_trade_alerts: bool = True
    notify_reports: bool = True

    @property
    def configured(self) -> bool:
        """True when the minimum set of SMTP fields is present and usable."""
        return bool(self.host) and bool(self.user) and _is_configured_secret(self.password)

    @property
    def effective_from(self) -> str:
        """The From address, falling back to the auth user."""
        return self.from_addr.strip() or self.user.strip()

    @property
    def effective_port(self) -> int:
        """Port, defaulting by host scheme when unset."""
        if self.port > 0:
            return self.port
        # 465 is implicit-TLS (SMTPS); everything else upgrades via STARTTLS.
        return 465 if self.use_tls else 587

    def masked_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict with the password masked.

        Used by the settings API GET endpoint. The password is reported as the
        sentinel ``"***"`` when a real secret is present, and ``""`` otherwise,
        so the frontend can distinguish "configured but hidden" from "empty".
        """
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": SMTP_SECRET_MASK if _is_configured_secret(self.password) else "",
            "password_configured": _is_configured_secret(self.password),
            "from_addr": self.from_addr,
            "use_tls": self.use_tls,
            "recipients": list(self.recipients),
            "notify_trade_alerts": self.notify_trade_alerts,
            "notify_reports": self.notify_reports,
            "configured": self.configured,
        }


def _split_recipients(raw: str) -> tuple[str, ...]:
    """Split a comma/semicolon-separated recipient list, dropping empties."""
    if not raw:
        return ()
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return tuple(p for p in parts if p)


def _env_truthy(value: str | None) -> bool:
    """Return True for common truthy env values (1/true/yes/on)."""
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_email_config(env: Mapping[str, str] | None = None) -> EmailConfig:
    """Resolve an :class:`EmailConfig` from the environment.

    Args:
        env: Optional explicit environment mapping (defaults to ``os.environ``).
            Tests pass a dict; production reads the live process environment
            populated from ``agent/.env`` at startup.

    Returns:
        The resolved config. ``configured`` is False when required fields are
        missing — callers should short-circuit silently in that case.
    """
    source = env if env is not None else os.environ
    raw_port = (source.get("SMTP_PORT") or "").strip()
    try:
        port = int(raw_port) if raw_port else 0
    except ValueError:
        port = 0
    return EmailConfig(
        host=(source.get("SMTP_HOST") or "").strip(),
        port=port,
        user=(source.get("SMTP_USER") or "").strip(),
        password=source.get("SMTP_PASSWORD") or "",
        from_addr=(source.get("SMTP_FROM") or "").strip(),
        use_tls=_env_truthy(source.get("SMTP_USE_TLS")) or source.get("SMTP_USE_TLS") is None,
        recipients=_split_recipients(source.get("NOTIFY_RECIPIENTS") or ""),
        notify_trade_alerts=_env_truthy(source.get("NOTIFY_TRADE_ALERTS"))
        or source.get("NOTIFY_TRADE_ALERTS") is None,
        notify_reports=_env_truthy(source.get("NOTIFY_REPORTS"))
        or source.get("NOTIFY_REPORTS") is None,
    )


def is_email_configured(env: Mapping[str, str] | None = None) -> bool:
    """Convenience: is SMTP configured well enough to send?"""
    return load_email_config(env).configured
