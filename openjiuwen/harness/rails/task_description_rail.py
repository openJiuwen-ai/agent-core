# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskDescriptionRail — pin a task-description file as a permanent prompt section.

Reads a task-description file from disk and pins its content as a dedicated
system-prompt section on every model call. Because the section lives in the
system prompt rather than the conversation history it is never compressed
away, so the agent always has the original task visible regardless of how
long the conversation has grown.

Typical use-case: CI pipelines or evaluation harnesses where the task is
written to a file (e.g. ``/app/task.md``) before the agent starts. The path is
supplied by the integrator that instantiates the rail; there is no agent-core
config coupling.

Note: this is distinct from the ``task_description`` values used elsewhere
(symphony flows, the subagent ``task_tool``, intents) — those describe a
sub-task argument; this rail pins a *file* that holds the whole task.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail

# Section priority 12 — after IDENTITY (10) and before SAFETY (20), so the
# agent reads the task description right after learning who it is, before
# the safety/rules sections.
_SECTION_PRIORITY = 12


class TaskDescriptionRail(DeepAgentRail):
    """Pin a task-description file as a permanent system-prompt section.

    ``priority`` (80) matches ``HeartbeatRail``, another section-injecting
    rail, so it initializes after the tool/planning rails (90–95) and before
    the resilience/evolution tiers (70–60).

    Lifecycle
    ---------
    * ``before_invoke``  — clears any previous section and attempts a fresh
      read at the start of each agent invocation.
    * ``before_model_call`` — retries the read/inject if the file was not
      available at invoke time (e.g. mounted asynchronously).
    """

    priority = 80

    def __init__(self, task_path: str) -> None:
        super().__init__()
        self._task_path = task_path
        self._injected: bool = False
        self.system_prompt_builder = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def init(self, agent: Any) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.TASK_DESCRIPTION)
        self.system_prompt_builder = None

    # ── rail hooks ───────────────────────────────────────────────────────────

    async def before_invoke(self, ctx: AgentCallbackContext, **kwargs: Any) -> None:
        """Clear stale section and re-read the file at invocation start."""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.TASK_DESCRIPTION)
        self._injected = False
        self._try_inject()

    async def before_model_call(self, ctx: AgentCallbackContext, **kwargs: Any) -> None:
        """Retry injection if the file was not available at invoke time."""
        if not self._injected:
            self._try_inject()

    # ── internals ────────────────────────────────────────────────────────────

    def _try_inject(self) -> None:
        if self.system_prompt_builder is None:
            return
        content = self._read_file()
        if not content:
            # No usable file (missing or empty) → do not mark injected, so
            # before_model_call keeps retrying until the file is populated.
            return
        body = f"# Task Description\n\n{content}"
        self.system_prompt_builder.add_section(
            PromptSection(
                name=SectionName.TASK_DESCRIPTION,
                content={"cn": body, "en": body},
                priority=_SECTION_PRIORITY,
            )
        )
        self._injected = True
        logger.debug(
            "[TaskDescriptionRail] Injected task description from %s (%d chars)",
            self._task_path,
            len(content),
        )

    def _read_file(self) -> str | None:
        try:
            path = Path(self._task_path)
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning(
                "[TaskDescriptionRail] Could not read task file %s: %s",
                self._task_path,
                exc,
            )
        return None


__all__ = ["TaskDescriptionRail"]
