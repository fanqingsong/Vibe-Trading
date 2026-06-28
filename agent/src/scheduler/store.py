"""SQLAlchemy-backed CRUD for :class:`ScheduledTask`.

All access is scoped by ``user_id`` so users can never see or mutate each
other's tasks in multi-user deployments. In the inert (no-DB) dev mode the
store is backed by an in-memory dict so the feature still works end-to-end on
a loopback localhost setup without provisioning a database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from src.db.base import get_session
from src.db.models import ScheduledTask

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_db_active() -> bool:
    """Return True when a real SQLAlchemy backend is configured."""
    from src.db.base import is_db_enabled

    return is_db_enabled()


class ScheduledTaskStore:
    """Persistence layer for scheduled tasks.

    When a database is configured (``DATABASE_URL`` set) rows live in the
    ``scheduled_tasks`` table. When the DB layer is inert (dev / loopback mode)
    an in-memory dict keyed by ``id`` keeps the same surface working so the
    feature does not require a database on localhost.

    The in-memory fallback is **per-process** and not durable across restarts.
    That matches the existing alpha-bench job store contract and is acceptable
    for a dev box; production deployments configure a DB.
    """

    def __init__(self) -> None:
        # Lazily detected so a test that flips DATABASE_URL before the first
        # call picks up the right backend.
        self._mem: dict[str, ScheduledTask] | None = None

    # ------------------------------------------------------------------ #
    # Backend selection
    # ------------------------------------------------------------------ #

    def _mem_backend(self) -> dict[str, ScheduledTask]:
        """Return (and lazily init) the in-memory fallback dict."""
        if self._mem is None:
            self._mem = {}
        return self._mem

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #

    def list_tasks(self, user_id: str, *, enabled_only: bool = False) -> list[ScheduledTask]:
        """Return all tasks owned by ``user_id``, newest first.

        Args:
            user_id: Owner filter.
            enabled_only: When True, return only enabled tasks.
        """
        if _is_db_active():
            return self._list_db(user_id, enabled_only=enabled_only)
        return [
            t
            for t in self._mem_backend().values()
            if t.user_id == user_id and (not enabled_only or t.enabled)
        ]

    def get_task(self, task_id: str, user_id: str) -> ScheduledTask | None:
        """Return one task if it exists and belongs to ``user_id``."""
        if _is_db_active():
            return self._get_db(task_id, user_id)
        t = self._mem_backend().get(task_id)
        if t is None or t.user_id != user_id:
            return None
        return t

    def get_all_enabled_tasks(self) -> list[ScheduledTask]:
        """Return every enabled task across all users.

        Used by the scheduler bootstrap on startup; the caller then re-adds
        each as a scheduler job. Not user-scoped by design.
        """
        if _is_db_active():
            return self._list_all_enabled_db()
        return [t for t in self._mem_backend().values() if t.enabled]

    def get_task_by_session_id(self, session_id: str) -> ScheduledTask | None:
        """Return the task bound to ``session_id`` if any (across all users).

        Each scheduled task owns a dedicated session, so a session_id maps to at
        most one task. Used by the session runtime to detect when an
        ``attempt.completed`` event belongs to a scheduled task and should
        trigger an email notification. Returns ``None`` for ordinary chat
        sessions. Not user-scoped by design.
        """
        if not session_id:
            return None
        if _is_db_active():
            return self._get_by_session_db(session_id)
        for t in self._mem_backend().values():
            if t.session_id == session_id:
                return t
        return None

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #

    def create_task(self, task: ScheduledTask) -> ScheduledTask:
        """Persist a brand-new task."""
        if _is_db_active():
            return self._create_db(task)
        self._mem_backend()[task.id] = task
        return task

    def update_task(self, task: ScheduledTask) -> ScheduledTask:
        """Persist updates to an existing task in place."""
        if _is_db_active():
            return self._update_db(task)
        # In-memory: the caller mutates the same instance we hold.
        self._mem_backend()[task.id] = task
        return task

    def delete_task(self, task_id: str, user_id: str) -> bool:
        """Delete a task. Returns True if a row was removed."""
        if _is_db_active():
            return self._delete_db(task_id, user_id)
        existing = self._mem_backend().get(task_id)
        if existing is None or existing.user_id != user_id:
            return False
        self._mem_backend().pop(task_id, None)
        return True

    # ------------------------------------------------------------------ #
    # DB-backed implementations
    # ------------------------------------------------------------------ #

    def _list_db(self, user_id: str, *, enabled_only: bool) -> list[ScheduledTask]:
        with get_session() as session:  # type: ignore[assignment]
            if session is None:  # pragma: no cover — guarded by _is_db_active
                return []
            q = session.query(ScheduledTask).filter(ScheduledTask.user_id == user_id)
            if enabled_only:
                q = q.filter(ScheduledTask.enabled.is_(True))
            rows = q.order_by(ScheduledTask.created_at.desc()).all()
            # Detach so callers can read attributes after the session closes.
            for r in rows:
                session.expunge(r)
            return rows

    def _get_db(self, task_id: str, user_id: str) -> ScheduledTask | None:
        with get_session() as session:  # type: ignore[assignment]
            if session is None:  # pragma: no cover
                return None
            row = (
                session.query(ScheduledTask)
                .filter(ScheduledTask.id == task_id, ScheduledTask.user_id == user_id)
                .one_or_none()
            )
            if row is not None:
                session.expunge(row)
            return row

    def _list_all_enabled_db(self) -> list[ScheduledTask]:
        with get_session() as session:  # type: ignore[assignment]
            if session is None:  # pragma: no cover
                return []
            rows = (
                session.query(ScheduledTask)
                .filter(ScheduledTask.enabled.is_(True))
                .all()
            )
            for r in rows:
                session.expunge(r)
            return rows

    def _get_by_session_db(self, session_id: str) -> ScheduledTask | None:
        with get_session() as session:  # type: ignore[assignment]
            if session is None:  # pragma: no cover
                return None
            row = (
                session.query(ScheduledTask)
                .filter(ScheduledTask.session_id == session_id)
                .one_or_none()
            )
            if row is not None:
                session.expunge(row)
            return row

    def _create_db(self, task: ScheduledTask) -> ScheduledTask:
        with get_session() as session:  # type: ignore[assignment]
            if session is None:  # pragma: no cover
                raise RuntimeError("DB not active")
            session.add(task)
            session.flush()
            session.expunge(task)
            return task

    def _update_db(self, task: ScheduledTask) -> ScheduledTask:
        with get_session() as session:  # type: ignore[assignment]
            if session is None:  # pragma: no cover
                raise RuntimeError("DB not active")
            # Merge detached instance back into the session and commit.
            merged = session.merge(task)
            session.flush()
            session.expunge(merged)
            return merged

    def _delete_db(self, task_id: str, user_id: str) -> bool:
        with get_session() as session:  # type: ignore[assignment]
            if session is None:  # pragma: no cover
                return False
            row = (
                session.query(ScheduledTask)
                .filter(ScheduledTask.id == task_id, ScheduledTask.user_id == user_id)
                .one_or_none()
            )
            if row is None:
                return False
            try:
                session.delete(row)
                return True
            except SQLAlchemyError:
                logger.exception("failed to delete scheduled task %s", task_id)
                return False
