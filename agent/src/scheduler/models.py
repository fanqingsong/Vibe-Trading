"""Pydantic request/response schemas for the scheduled-task API.

Kept separate from the ORM model in ``src.db.models`` so the wire contract can
evolve independently of the on-disk schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.scheduler.cron import PRESET_TO_CRON, VALID_PRESETS, resolve_cron

ScheduleType = Literal["preset", "cron"]
OverlapPolicy = Literal["skip", "queue", "replace"]
TaskStatus = Literal["idle", "running", "success", "failed", "skipped"]


class ScheduleSpec(BaseModel):
    """Schedule definition shared by create/update payloads.

    Exactly one of ``preset`` (when ``type == "preset"``) or ``cron`` (when
    ``type == "cron"``) must be provided.
    """

    type: ScheduleType = Field(..., description='"preset" or "cron"')
    preset: str | None = Field(
        None, description="Preset key; required when type == 'preset'"
    )
    cron: str | None = Field(
        None,
        description="5-field cron expression; required when type == 'cron'",
        max_length=128,
    )
    timezone: str = Field(
        "Asia/Shanghai", description="IANA timezone the schedule is expressed in"
    )

    @field_validator("preset")
    @classmethod
    def _preset_known(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in VALID_PRESETS:
            raise ValueError(
                f"unknown preset {v!r}; expected one of {sorted(PRESET_TO_CRON.keys())}"
            )
        return v

    def resolved_cron(self) -> str:
        """Return the canonical cron expression, validating the spec.

        Raises:
            ValueError: If the schedule is malformed.
        """
        return resolve_cron(
            schedule_type=self.type, preset=self.preset, cron_expr=self.cron
        )


class TaskCreate(BaseModel):
    """POST /scheduler/tasks body."""

    title: str = Field(..., min_length=1, max_length=255)
    prompt: str = Field(..., min_length=1)
    schedule: ScheduleSpec
    session_id: str | None = Field(
        None,
        description="Optional existing session id; auto-created when omitted",
        max_length=64,
    )
    on_overlap: OverlapPolicy = "skip"
    notify_enabled: bool = Field(
        False, description="Email the agent's reply to the owner after each fire"
    )
    notify_emails: str | None = Field(
        None,
        description="Optional comma/semicolon-separated recipient override",
        max_length=512,
    )


class TaskUpdate(BaseModel):
    """PATCH /scheduler/tasks/{id} body — all fields optional."""

    title: str | None = Field(None, min_length=1, max_length=255)
    prompt: str | None = Field(None, min_length=1)
    schedule: ScheduleSpec | None = None
    enabled: bool | None = None
    on_overlap: OverlapPolicy | None = None
    timezone: str | None = Field(None, max_length=64)
    notify_enabled: bool | None = None
    notify_emails: str | None = Field(None, max_length=512)


class TaskOut(BaseModel):
    """Scheduled task on the wire."""

    id: str
    user_id: str
    title: str
    prompt: str
    schedule_type: str
    schedule_preset: str | None = None
    cron_expr: str | None = None
    timezone: str
    session_id: str
    enabled: bool
    on_overlap: str
    notify_enabled: bool = False
    notify_emails: str | None = None
    last_run_at: str | None = None
    last_status: str
    last_error: str | None = None
    last_attempt_id: str | None = None
    run_count: int
    created_at: str
    updated_at: str
    next_run_at: str | None = None
    schedule_label: str | None = None
