"""Generic scheduled-task subsystem.

Lets a user describe a prompt and a schedule; the scheduler fires the prompt at
the agent through the standard ``SessionService.send_message`` entry at the
configured times. Reuses the live-runtime :class:`Scheduler` /
:class:`Scheduler` wall-clock core, but with a lightweight :class:`PromptRunner`
that has no mandate / broker coupling.

Import the exact module you need (e.g.
``from src.scheduler.service import SchedulerService``) — sibling modules land
independently and should not be pulled through the package ``__init__``.
"""
