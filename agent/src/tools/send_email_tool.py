"""Send email tool: LLM-initiated outbound email via configured SMTP.

Wraps :func:`src.notify.mailer.send_email_sync` so the agent can email analysis
results, reports, or alerts to a user-specified recipient (or the global
``NOTIFY_RECIPIENTS`` list when no address is given).

The tool is always registered; when SMTP is not configured it returns a clear,
actionable error so the agent can tell the user how to enable the feature
rather than silently failing.
"""

from __future__ import annotations

import html as _html
from typing import Any, Iterable

from src.agent.tools import BaseTool
from src.notify.config import load_email_config
from src.notify.mailer import send_email_sync


def _coerce_recipients(value: Any) -> list[str]:
    """Normalize a recipient input into a clean list of addresses.

    Accepts a single address string, a comma/semicolon-separated string, or an
    iterable of strings. Empty / whitespace-only entries are dropped.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
    elif isinstance(value, Iterable):
        parts = list(value)
    else:
        return []
    return [str(p).strip() for p in parts if str(p).strip()]


def _text_to_html(text: str) -> str:
    """Render a plain-text body as readable HTML.

    Escapes HTML-significant characters and wraps the text in a ``pre-wrap``
    block so indentation, lists, and ASCII tables survive in email clients
    that would otherwise collapse whitespace.
    """
    escaped = _html.escape(text)
    return (
        '<div style="white-space: pre-wrap; font-family: -apple-system, '
        "'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; "
        'font-size: 14px; line-height: 1.6; color: #1f2329;">'
        f"{escaped}</div>"
    )


class SendEmailTool(BaseTool):
    """Send an email with a subject and body to one or more recipients.

    Requires SMTP to be configured in ``agent/.env`` (``SMTP_HOST`` /
    ``SMTP_USER`` / ``SMTP_PASSWORD``). When no ``to`` is given, the message
    goes to the global ``NOTIFY_RECIPIENTS`` list.
    """

    name = "send_email"
    description = (
        "Send an email to a recipient (or the default NOTIFY_RECIPIENTS list). "
        "Use this to deliver analysis results, reports, or summaries to the "
        "user's inbox. The body may be plain text (preserving line breaks and "
        "indentation) or HTML."
    )
    is_readonly = False
    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": (
                    "Recipient email address(es). A single address, or a "
                    "comma/semicolon-separated list. If omitted, the message "
                    "is sent to the configured NOTIFY_RECIPIENTS list."
                ),
            },
            "subject": {
                "type": "string",
                "description": "Email subject line (plain text).",
            },
            "body": {
                "type": "string",
                "description": "Email body. Plain text by default.",
            },
            "body_format": {
                "type": "string",
                "enum": ["text", "html"],
                "description": (
                    "How to treat `body`. 'text' (default) escapes and "
                    "preserves whitespace; 'html' uses the body verbatim."
                ),
            },
        },
        "required": ["subject", "body"],
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        """Send one email synchronously and return a JSON status string.

        SMTP-level failures are captured (never raised) and surfaced as a
        ``status: "error"`` result so the agent can retry or report the
        problem to the user.
        """
        import json

        subject = (kwargs.get("subject") or "").strip()
        body = kwargs.get("body") or ""
        body_format = (kwargs.get("body_format") or "text").strip().lower()

        if not subject:
            return json.dumps(
                {"status": "error", "error": "subject is required"},
                ensure_ascii=False,
            )
        if not body:
            return json.dumps(
                {"status": "error", "error": "body is required"},
                ensure_ascii=False,
            )

        cfg = load_email_config()
        if not cfg.configured:
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        "Email is not configured. Set SMTP_HOST, SMTP_USER, and "
                        "SMTP_PASSWORD in agent/.env (or via the Web UI Settings "
                        "page) to enable sending."
                    ),
                },
                ensure_ascii=False,
            )

        recipients = _coerce_recipients(kwargs.get("to")) or list(cfg.recipients)
        if not recipients:
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        "No recipient specified and NOTIFY_RECIPIENTS is empty. "
                        "Provide a `to` address or configure NOTIFY_RECIPIENTS."
                    ),
                },
                ensure_ascii=False,
            )

        html_body = body if body_format == "html" else _text_to_html(body)

        result = send_email_sync(
            to=recipients,
            subject=subject,
            html=html_body,
            config=cfg,
        )

        payload: dict[str, Any] = {
            "status": "ok" if result.ok else "error",
            "ok": result.ok,
            "message": result.message,
            "recipients": result.recipients,
            "subject": result.subject,
            "latency_ms": result.latency_ms,
        }
        if result.error:
            payload["error"] = result.error
        return json.dumps(payload, ensure_ascii=False)
