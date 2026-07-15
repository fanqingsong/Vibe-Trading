"""Generic scheduled-task subsystem.

Lets a user describe a prompt and a schedule; the scheduler runs the prompt
directly through the agent (no chat Session) at the configured times. Reuses
the live-runtime wall-clock :class:`Scheduler`, with a lightweight
:class:`PromptRunner` that has no mandate / broker coupling.

Import the exact module you need (e.g.
``from src.scheduler.service import SchedulerService``) — sibling modules land
independently and should not be pulled through the package ``__init__``.
"""
