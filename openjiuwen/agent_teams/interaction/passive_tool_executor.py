# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool-call passthrough executor for passive human members.

A passive human member has no avatar — no harness, no LLM, no
coordination loop. Its "perceive → decide → act" loop lives outside the
runtime: the external channel (SDK / business protocol) shows the human
the team's messages and task notifications, and relays back the actions
to take as :class:`~openjiuwen.agent_teams.interaction.payload.HumanAgentToolCall`
payloads. This module is the act half: given ``(sender, tool_name,
tool_args)``, it resolves a tool surface **bound to the sender's member
identity** and executes the call verbatim.

Identity binding is the whole point. The executor builds per-sender
``TeamTaskManager`` / ``TeamMessageManager`` instances — exactly what an
avatar gets via ``setup_team_backend`` (agent_configurator.py) and what
the scheduler's temp reviewer gets (scheduler.py) — so every identity
guard inside the tools works unchanged:

* ``member_complete_task`` refuses tasks whose ``assignee`` differs from
  the caller (``task_manager.member_name``);
* ``verify_task`` refuses verdicts from a non-reviewer of the task;
* ``send_message`` rows carry the passive member as the sender.

The permission face is ``PASSIVE_HUMAN_TOOLS`` (see tool_permissions.py):
the human-member tool set plus ``claim_task``. Under scheduled dispatch
the executor subtracts ``claim_task`` and swaps ``send_message`` for its
report-to-leader form, mirroring the ``MEMBER_ONLY_TOOLS_SCHEDULED``
convention that the leader assigns all work.

Tool instances are cached per sender for the lifetime of the executor
(construction is pure in-memory; the caches die with the leader runtime
that owns the executor). Execution failures are returned, never raised:
one bad external call must not break the dispatch loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.team import TeamBackend


class PassiveToolExecutor:
    """Execute relayed team tool calls under a passive member's identity.

    Owned by the leader runtime (one executor per active team session);
    ``TeamRuntimeManager._dispatch_payload`` delegates the
    ``HumanAgentToolCall`` branch here after validating the sender is a
    passive human member.
    """

    def __init__(self, backend: "TeamBackend", *, language: str = "cn") -> None:
        self._backend = backend
        self._language = language
        self._tool_cache: dict[str, dict[str, Tool]] = {}

    async def execute(self, sender: str, tool_name: str, tool_args: dict[str, Any]) -> ToolOutput:
        """Run one tool call as ``sender``; never raises.

        Args:
            sender: The passive human member whose identity the call runs under.
            tool_name: Team tool name (must be within the permission face).
            tool_args: Arguments matching the tool's ``input_params`` schema.

        Returns:
            The tool's ``ToolOutput``. Unknown / unpermitted tools and
            internal errors come back as ``ToolOutput(success=False)``
            with a descriptive error string.
        """
        try:
            tools = await self._tools_for(sender)
        except Exception as exc:
            team_logger.error(
                "passive tool executor: failed to build tool surface for {}: {}",
                sender,
                exc,
            )
            return ToolOutput(success=False, error=f"Failed to build tool surface: {exc}")

        tool = tools.get(tool_name)
        if tool is None:
            permitted = sorted(tools)
            return ToolOutput(
                success=False,
                error=(
                    f"Unknown or unpermitted tool '{tool_name}' for passive member "
                    f"'{sender}'; permitted tools: {permitted}"
                ),
            )

        try:
            return await tool.invoke(tool_args or {})
        except Exception as exc:
            team_logger.error(
                "passive tool executor: {} as {} raised: {}",
                tool_name,
                sender,
                exc,
            )
            return ToolOutput(success=False, error=f"Tool '{tool_name}' raised: {exc}")

    def map_output(self, tool_name: str, output: ToolOutput) -> str:
        """Render one tool's ``ToolOutput`` to model-facing text.

        Uses the tool's own ``map_result`` where available so the text an
        external protocol sees matches what an LLM caller would have seen.
        """
        tools = next(iter(self._tool_cache.values()), {})
        tool = tools.get(tool_name)
        if tool is not None and hasattr(tool, "map_result"):
            try:
                return tool.map_result(output)
            except Exception as exc:
                # A tool's own rendering is best-effort: fall back to the plain
                # text rather than failing the passthrough, but say why.
                team_logger.warning(
                    "[passive-human] tool {} failed to render its result: {}",
                    tool_name,
                    exc,
                )
        return str(output)

    async def _tools_for(self, sender: str) -> dict[str, Tool]:
        """Resolve (and cache) the sender-bound tool surface."""
        cached = self._tool_cache.get(sender)
        if cached is not None:
            return cached

        from openjiuwen.agent_teams.tools.locales import make_translator
        from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
        from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
        from openjiuwen.agent_teams.tools.tool_factory import (
            _MEMBER_COMPLETE_DESC_KEY,
            _SEND_MESSAGE_CLASS,
            _VERIFY_TASK_DESC_KEY,
        )
        from openjiuwen.agent_teams.tools.tool_permissions import PASSIVE_HUMAN_TOOLS
        from openjiuwen.agent_teams.tools.tool_task import (
            ClaimTaskTool,
            MemberCompleteTaskTool,
            VerifyTaskTool,
            ViewTaskToolV2,
        )

        backend = self._backend
        dispatch_mode = getattr(backend, "dispatch_mode", "autonomous")
        # Tool descriptions resolve evolved values through the team's
        # resident WorkspaceCache, same as every other tool-construction
        # site (scheduler temp reviewers, the tool factory).
        t = make_translator(self._language, ws_cache=backend.workspace_cache)

        task_manager: TeamTaskManager = TeamTaskManager(
            backend.team_name,
            sender,
            backend.db,
            backend.messager,
            dispatch_mode=dispatch_mode,
        )
        message_manager = TeamMessageManager(
            backend.team_name,
            sender,
            backend.db,
            backend.messager,
        )

        allowed = set(PASSIVE_HUMAN_TOOLS)
        if dispatch_mode == "scheduled":
            # The leader assigns all work under scheduled dispatch — the
            # autonomous claim path has no meaning, mirroring
            # MEMBER_ONLY_TOOLS_SCHEDULED.
            allowed = allowed - {"claim_task"}

        tools: dict[str, Tool] = {}
        if "view_task" in allowed:
            tools["view_task"] = ViewTaskToolV2(task_manager, t)
        if "member_complete_task" in allowed:
            tools["member_complete_task"] = MemberCompleteTaskTool(
                task_manager,
                t,
                desc_key=_MEMBER_COMPLETE_DESC_KEY[dispatch_mode],
            )
        if "verify_task" in allowed:
            tools["verify_task"] = VerifyTaskTool(
                task_manager,
                t,
                desc_key=_VERIFY_TASK_DESC_KEY[dispatch_mode],
            )
        if "claim_task" in allowed:
            tools["claim_task"] = ClaimTaskTool(task_manager, t)
        if "send_message" in allowed:
            send_cls = _SEND_MESSAGE_CLASS[(dispatch_mode, "member")]
            tools["send_message"] = send_cls(message_manager, t, team=backend)

        self._tool_cache[sender] = tools
        return tools


__all__ = ["PassiveToolExecutor"]
