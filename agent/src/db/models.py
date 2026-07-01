"""ORM models for the auth/account subsystem, scheduler tasks, and settings."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    """An application user account.

    Attributes:
        id: Stable opaque identifier (UUID hex). Used to namespace per-user file
            storage (sessions/runs/uploads live under ``{root}/{user_id}/...``).
        email: Unique login identifier, stored case-normalized (lowercased).
        name: Optional display name.
        hashed_password: bcrypt hash.
        is_active: Soft-disable flag.
        is_admin: Whether the user may manage other accounts (reserved).
        created_at: Account creation time (UTC).
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    def to_public_dict(self) -> dict:
        """Return a JSON-safe dict with no secrets (for API responses)."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} email={self.email!r}>"


class ScheduledTask(Base):
    """A user-owned generic scheduled task that runs a prompt on a schedule.

    The task body is a natural-language ``prompt`` handed to the agent via the
    standard ``SessionService.send_message`` entry when the schedule fires. The
    schedule is expressed in one of two modes (``schedule_type``):

    * ``"preset"`` — a named preset from :data:`PRESET_TO_CRON` (e.g.
      ``"daily_0930"``), stored in ``schedule_preset``.
    * ``"cron"`` — a standard 5-field cron expression in ``cron_expr``.

    Both modes are projected onto an equivalent cron string for next-fire
    computation, so the scheduler only needs to understand one form.

    Execution lands in a dedicated session (one per task) so the full agent
    transcript (prompt + streamed answer + tool calls) is preserved and
    replayable from the Web UI exactly like a manual chat turn.

    Attributes:
        id: Stable opaque identifier (UUID hex).
        user_id: Owner. Used for per-user isolation in multi-user deployments.
        title: Human-readable label shown in the management UI.
        prompt: The task body handed to the agent at each fire.
        schedule_type: ``"preset"`` or ``"cron"``.
        schedule_preset: Preset key when ``schedule_type == "preset"``.
        cron_expr: 5-field cron expression when ``schedule_type == "cron"``.
        timezone: IANA tz the schedule is interpreted in (default Asia/Shanghai).
        session_id: Dedicated session id the prompt is sent to on each fire.
        enabled: When False the task is skipped (scheduler still holds the job
            so toggling back to True is instant).
        on_overlap: Concurrency policy when the previous run is still active.
            ``"skip"`` (default) drops the new fire; ``"queue"`` / ``"replace"``
            are reserved for future use.
        last_run_at: Wall-clock time of the most recent fire (any outcome).
        last_status: Terminal status of the most recent fire:
            ``idle|running|success|failed|skipped``.
        last_error: Short error string when ``last_status == "failed"``.
        last_attempt_id: The session attempt id produced by the last fire, so
            the UI can deep-link into the agent transcript.
        run_count: Monotonic counter of completed fires.
        created_at / updated_at: Timestamps (UTC).
    """

    __tablename__ = "scheduled_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)

    schedule_type: Mapped[str] = mapped_column(String(16), nullable=False)
    schedule_preset: Mapped[str | None] = mapped_column(String(32))
    cron_expr: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)

    session_id: Mapped[str] = mapped_column(String(64), nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    on_overlap: Mapped[str] = mapped_column(String(16), default="skip", nullable=False)

    # Per-task email-notification controls. ``notify_enabled`` defaults off so
    # existing tasks never start sending mail unprompted. ``notify_emails`` is an
    # optional comma/semicolon-separated recipient override; when empty the
    # owner's ``User.email`` (or the global ``NOTIFY_RECIPIENTS``) is used.
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_emails: Mapped[str | None] = mapped_column(String(512))

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String(16), default="idle", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_attempt_id: Mapped[str | None] = mapped_column(String(64))
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def to_public_dict(self) -> dict:
        """Return a JSON-safe dict for API responses."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "prompt": self.prompt,
            "schedule_type": self.schedule_type,
            "schedule_preset": self.schedule_preset,
            "cron_expr": self.cron_expr,
            "timezone": self.timezone,
            "session_id": self.session_id,
            "enabled": bool(self.enabled),
            "on_overlap": self.on_overlap,
            "notify_enabled": bool(self.notify_enabled),
            "notify_emails": self.notify_emails,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_attempt_id": self.last_attempt_id,
            "run_count": self.run_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ScheduledTask id={self.id} title={self.title!r} enabled={self.enabled}>"


class Setting(Base):
    """Global application setting stored as key-value pairs grouped by category.

    Categories: 'llm', 'data_source', 'email'. Within a category, each row is
    one configuration key (e.g. LLM_PROVIDER, SMTP_HOST). Secret values
    (API keys, passwords) are stored in plaintext — the DB itself is the trust
    boundary, mirroring how they previously lived in agent/.env.

    This table is global (no user_id): LLM provider, data-source tokens, and
    SMTP config are shared system-wide. ``is_secret`` marks rows whose value
    must never be echoed back in API responses (callers surface a boolean
    ``*_configured`` flag instead).
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("category", "key", name="uq_settings_category_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Setting category={self.category!r} key={self.key!r}>"
