"""Email notification subsystem.

Public surface (imported blind by api_server / session service):

* :func:`send_email`       — async send one HTML email (low-level).
* :func:`dispatch_event`   — map a runtime event to an email + send (throttled).
* :func:`render_template`  — render a named email template to HTML.
* :class:`EmailConfig`     — SMTP settings resolved from env / ``agent/.env``.

Design: zero external mail SaaS SDK. SMTP via :mod:`aiosmtplib` (async, matches
the FastAPI style). HTML via Jinja2, mirroring ``shadow_account/reporter.py``.
"""

from src.notify.config import EmailConfig, load_email_config, is_email_configured
from src.notify.mailer import (
    EmailResult,
    send_email,
    send_email_sync,
    send_test_email,
)
from src.notify.dispatcher import dispatch_event
from src.notify.renderer import render_template

__all__ = [
    "EmailConfig",
    "load_email_config",
    "is_email_configured",
    "EmailResult",
    "send_email",
    "send_email_sync",
    "send_test_email",
    "dispatch_event",
    "render_template",
]
