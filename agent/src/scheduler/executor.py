"""Direct agent execution for scheduled tasks (no Session / chat transcript).

Scheduled tasks are not conversations. They invoke :class:`AgentLoop` with the
task prompt, await the result, and return a status dict. Nothing is written to
a Session, Message, or Attempt row — the PromptRunner records the outcome on
the :class:`ScheduledTask` itself.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Dedicated pool so scheduled fires do not contend with interactive chat
#: agents on :data:`src.session.service._AGENT_EXECUTOR`.
_SCHEDULER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="sched-agent"
)


async def run_scheduled_prompt(prompt: str, *, task_id: str = "") -> dict[str, Any]:
    """Run ``prompt`` through the agent and return the loop result dict.

    Args:
        prompt: The task body to execute.
        task_id: Opaque id used only for logging / run metadata (not a
            session id — no chat history or SSE bus is involved).

    Returns:
        The :meth:`AgentLoop.run` result, typically containing
        ``status``, ``content``, ``run_id``, ``run_dir``, and optional
        ``reason``.
    """
    from src.agent.loop import AgentLoop
    from src.config.loader import load_runtime_agent_config
    from src.memory.persistent import PersistentMemory
    from src.providers.chat import ChatLLM
    from src.tools import build_registry

    loop = asyncio.get_running_loop()
    agent_config = load_runtime_agent_config()
    llm = ChatLLM()
    pm = PersistentMemory()

    def _build_and_run() -> dict[str, Any]:
        registry = build_registry(
            persistent_memory=pm,
            include_shell_tools=False,
            agent_config=agent_config,
            # Empty session_id: AgentLoop traces land under run_dir, not a
            # chat session. Tools that optionally key off session_id degrade
            # gracefully (goals / session-scoped MCP are skipped).
            session_id="",
            event_callback=None,
            warn_callback=None,
        )
        agent = AgentLoop(
            registry=registry,
            llm=llm,
            event_callback=None,
            max_iterations=50,
            persistent_memory=pm,
        )
        logger.info("scheduled agent run starting task_id=%s", task_id or "?")
        return agent.run(user_message=prompt, history=None, session_id="")

    return await loop.run_in_executor(_SCHEDULER_EXECUTOR, _build_and_run)
