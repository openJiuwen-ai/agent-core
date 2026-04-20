# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AgentModeRail — three-layer defense for plan mode enforcement.

Responsibilities:
1. Register ``enter_plan_mode`` / ``exit_plan_mode`` tools on init.
2. ``before_model_call``:
   - Inject MODE_INSTRUCTIONS system prompt section.
   - Remove Todo/Session sections added by higher-priority rails.
   - Filter Todo/Session tools from the visible tool list.
3. ``before_tool_call`` (three-segment):
   - Seg 1: validate and pass through enter/exit tools.
   - Seg 2: pass through everything when not in plan mode.
   - Seg 3: whitelist + path check + hard-block todo/session.
4. ``after_tool_call``:
   - On ``enter_plan_mode`` success: dynamically register task_tool.
   - On ``exit_plan_mode`` success: unregister self-owned task_tool.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.agent_mode import build_plan_mode_section
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools.agent_mode_tools import SwitchModeTool, EnterPlanModeTool, ExitPlanModeTool
from openjiuwen.harness.tools.task_tool import create_task_tool

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

# ---------------------------------------------------------------------------
# Tool name sets
# ---------------------------------------------------------------------------

_TODO_TOOL_NAMES = frozenset({"todo_create", "todo_list", "todo_modify"})
_SESSION_TOOL_NAMES = frozenset({"sessions_list", "sessions_cancel", "sessions_spawn"})
_HIDDEN_IN_PLAN = _TODO_TOOL_NAMES | _SESSION_TOOL_NAMES

_PLAN_FILE_WRITE_TOOLS = frozenset({"write_file", "edit_file"})

DEFAULT_PLAN_MODE_ALLOWED_TOOLS: tuple[str, ...] = (
    "switch_mode",
    "enter_plan_mode",
    "exit_plan_mode",
    "ask_user",
    "task_tool",
    "read_file",
    "grep",
    "list_files",
    "glob",
    "bash",
    "write_file",
    "edit_file",
)


class AgentModeRail(DeepAgentRail):
    """Rail that enforces read-only plan mode constraints.

    Always registered; activates conditionally based on
    ``DeepAgentState.plan_mode.mode == "plan"``.

    Priority 85 ensures this rail runs *after*
    TaskPlanningRail(90) / SessionRail(95) so it can remove sections
    those rails added.

    Args:
        allowed_tools: Whitelist of tool names permitted in plan mode.
            If ``None``, uses :data:`DEFAULT_PLAN_MODE_ALLOWED_TOOLS`.
    """

    priority = 85

    def __init__(self, allowed_tools: list[str] | None = None) -> None:
        super().__init__()
        names = (
            allowed_tools
            if allowed_tools is not None
            else list(DEFAULT_PLAN_MODE_ALLOWED_TOOLS)
        )
        self._allowed_tools: frozenset[str] = frozenset(names)
        self._owns_task_tool: bool = False
        self._task_tools: Optional[List[Tool]] = None
        self._owned_task_tool_names: set[str] = set()
        self._tools: List[Tool] = []
        self.system_prompt_builder = None

    def init(self, agent: "DeepAgent") -> None:
        """Register enter/exit tools and capture system_prompt_builder.

        Args:
            agent: The parent DeepAgent being initialized.
        """
        self._agent = agent
        self.system_prompt_builder = agent.system_prompt_builder
        language = self.system_prompt_builder.language

        self._tools = [
            SwitchModeTool(agent_ref=agent, language=language),
            EnterPlanModeTool(agent_ref=agent, language=language),
            ExitPlanModeTool(agent_ref=agent, language=language),
        ]
        for tool in self._tools:
            Runner.resource_mgr.add_tool(tool)
            agent.ability_manager.add(tool.card)

        logger.info("[AgentModeRail] Registered enter/exit plan mode tools")

    def uninit(self, agent: "DeepAgent") -> None:
        """Unregister all tools owned by this rail.

        Args:
            agent: The parent DeepAgent being torn down.
        """
        for tool in self._tools:
            try:
                agent.ability_manager.remove(tool.name)
                Runner.resource_mgr.remove_tool(tool.card.id)
            except Exception as exc:
                logger.warning(
                    f"[AgentModeRail] Failed to remove tool '{tool.name}': {exc}"
                )
        self._tools = []

        if self._owns_task_tool and self._task_tools:
            self._unregister_task_tool(agent)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject MODE_INSTRUCTIONS and filter hidden tools when in plan mode.

        Args:
            ctx: Callback context providing agent, session, and inputs.
        """
        agent = self._agent
        session = ctx.session
        plan_state = agent.load_state(session).plan_mode

        if plan_state.mode != "plan":
            self.system_prompt_builder.remove_section(SectionName.MODE_INSTRUCTIONS)
            self._sync_task_tool_for_model_tool_inputs(ctx)
            return

        # 1. Inject MODE_INSTRUCTIONS
        plan_file_path = agent.get_plan_file_path(session)
        plan_file_path_str = str(plan_file_path) if plan_file_path else ""
        plan_exists = plan_file_path.exists() if plan_file_path else False

        section = build_plan_mode_section(
            language=self.system_prompt_builder.language,
            plan_file_path=plan_file_path_str,
            plan_exists=plan_exists,
            agent=agent,
            session=session,
        )
        self.system_prompt_builder.add_section(section)

        # 2. Remove Todo/Session sections added by higher-priority rails
        self.system_prompt_builder.remove_section(SectionName.TODO)
        self.system_prompt_builder.remove_section(SectionName.SESSION_TOOLS)

        # 3. Filter hidden tools from the visible tool list
        if isinstance(ctx.inputs.tools, list):
            ctx.inputs.tools = [
                t for t in ctx.inputs.tools
                if getattr(t, "name", "") not in _HIDDEN_IN_PLAN
            ]

        self._sync_task_tool_for_model_tool_inputs(ctx)

    def _sync_task_tool_for_model_tool_inputs(self, ctx: AgentCallbackContext) -> None:
        """Sync task_tool visibility in model-visible tools.

        In react-agent flows, ``ctx.inputs.tools`` may be reused across turns.
        This keeps ``task_tool`` consistent with current registration state:
        - owned + registered: ensure present once
        - not owned: ensure absent

        Args:
            ctx: Callback context.
        """
        if not isinstance(ctx.inputs.tools, list):
            return
        if self._owns_task_tool and self._task_tools:
            existing_names = {
                getattr(t, "name", "")
                for t in ctx.inputs.tools
                if getattr(t, "name", None)
            }
            for tool in self._task_tools:
                if tool.card.name not in existing_names:
                    ctx.inputs.tools.append(tool.card.tool_info())
            return
        
        # only filter task_tool registered by plan mode rail
        if (not self._owns_task_tool) and self._owned_task_tool_names:
            ctx.inputs.tools = [
                t for t in ctx.inputs.tools
                if getattr(t, "name", "") not in self._owned_task_tool_names
            ]
            self._owned_task_tool_names.clear()

        return

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Intercept tool calls and enforce plan mode restrictions.

        Three segments:
        1. enter/exit tools — validate mode then pass through.
        2. Non-plan mode — pass through unconditionally.
        3. Plan mode — whitelist + path check + hard-block hidden tools.

        Args:
            ctx: Callback context.
        """
        agent = self._agent
        session = ctx.session
        tool_name = ctx.inputs.tool_name

        # ----------------------------------------------------------------
        # Segment 1: enter/exit_plan_mode — mode check + pass through
        # ----------------------------------------------------------------
        if tool_name == "enter_plan_mode":
            self._handle_enter(ctx)
            return
        if tool_name == "exit_plan_mode":
            self._handle_exit(ctx)
            return

        # ----------------------------------------------------------------
        # Segment 2: not in plan mode — pass through
        # ----------------------------------------------------------------
        plan_state = agent.load_state(session).plan_mode
        if plan_state.mode != "plan":
            return

        # ----------------------------------------------------------------
        # Segment 3: plan mode — whitelist + path check + hard-block
        # ----------------------------------------------------------------
        if ctx.extra.get("_skip_tool"):
            return

        # 3a. Hard-block todo/session tools (belt-and-suspenders)
        if tool_name in _HIDDEN_IN_PLAN:
            self._reject_tool(
                ctx,
                f"[PlanMode] Tool '{tool_name}' is hidden in plan mode.",
            )
            return

        # 3b. Not in whitelist → reject
        if self._allowed_tools and tool_name not in self._allowed_tools:
            logger.info("reject tool call by not in allowed tools")
            self._reject_tool(
                ctx,
                f"[PlanMode] Tool '{tool_name}' is not available in plan mode.",
            )
            return

        # 3c. write_file / edit_file → must target plan file only
        if tool_name in _PLAN_FILE_WRITE_TOOLS:
            file_path = self._extract_file_path(ctx)
            plan_path = agent.get_plan_file_path(session)
            plan_path_str = str(plan_path) if plan_path else ""
            if not self._is_plan_file(file_path, plan_path_str):
                logger.info("reject tool call by not in plan file")
                self._reject_tool(
                    ctx,
                    f"[PlanMode] '{tool_name}' can only target the plan file "
                    f"({plan_path_str}).",
                )
                return

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Dynamically register/unregister task_tool around enter/exit.

        Args:
            ctx: Callback context.
        """
        tool_name = ctx.inputs.tool_name
        agent = self._agent

        if tool_name == "enter_plan_mode" and not ctx.extra.get("_skip_tool"):
            self._register_task_tool(agent)

        elif tool_name == "exit_plan_mode" and not ctx.extra.get("_skip_tool"):
            self._unregister_task_tool(agent)

    def _is_task_tool_registered(self) -> bool:
        """Find if task_tool already registered in Runner."""
        tools = Runner.resource_mgr.get_tool()
        if tools is None:
            return False
        if not isinstance(tools, list):
            tools = [tools]
        return any(
            getattr(getattr(t, "card", None), "name", None) == "task_tool"
            for t in tools
        )

    def _register_task_tool(self, agent: "DeepAgent") -> None:
        """Register task_tool if not already present after enter_plan_mode.

        Args:
            agent: Parent DeepAgent.
        """
        if self._owns_task_tool:
            return
        existing = self._is_task_tool_registered()
        if existing:
            logger.info("[AgentModeRail] task tool already registered, skip register")
            return
        if not agent.deep_config.subagents:
            return

        available_agents = self._build_available_agents(agent.deep_config.subagents)
        self._task_tools = create_task_tool(
            parent_agent=agent,
            available_agents=available_agents,
            language=self.system_prompt_builder.language,
        )
        self._owned_task_tool_names = {
            tool.card.name for tool in (self._task_tools or [])
            if getattr(getattr(tool, "card", None), "name", None)
        }
        Runner.resource_mgr.add_tool(list(self._task_tools))
        for tool in self._task_tools:
            agent.ability_manager.add(tool.card)
        self._owns_task_tool = True
        logger.info("[AgentModeRail] Registered task_tool for plan mode")

    def _unregister_task_tool(self, agent: "DeepAgent") -> None:
        """Unregister only the task_tool that this rail owns.

        Args:
            agent: Parent DeepAgent.
        """
        if not self._owns_task_tool or not self._task_tools:
            logger.info("[AgentModeRail] no task tool registered, skip unregister")
            return
        for tool in self._task_tools:
            try:
                agent.ability_manager.remove(tool.card.name)
                Runner.resource_mgr.remove_tool(tool.card.id)
                logger.info("[AgentModeRail] Unregistered plan-mode task_tool")
            except Exception as exc:
                logger.warning(
                    f"[AgentModeRail] Failed to unregister task_tool '{tool.name}': {exc}"
                )
        self._task_tools = None
        self._owns_task_tool = False

    def _build_available_agents(
        self,
        subagents: list,
    ) -> str:
        """Build a formatted description of available sub-agents.

        Args:
            subagents: List of SubAgentConfig or DeepAgent instances.

        Returns:
            Newline-separated ``"name": description`` lines.
        """
        lines = []
        for spec in subagents:
            if isinstance(spec, SubAgentConfig):
                name = spec.agent_card.name
                desc = spec.agent_card.description
            else:
                card = getattr(spec, "card", None)
                name = getattr(card, "name", None) or "general-purpose"
                desc = getattr(card, "description", None) or "DeepAgent instance"
            lines.append(f'"{name}": {desc}')
        return "\n".join(lines)

    def _handle_enter(self, ctx: AgentCallbackContext) -> None:
        """Validate mode for enter_plan_mode and pass through if OK.

        Args:
            ctx: Callback context.
        """
        agent = self._agent
        session = ctx.session
        
        plan_state = agent.load_state(session).plan_mode

        if plan_state.mode != "plan":
            logger.info("reject enter tool because of not plan mode")
            self._reject_tool(
                ctx,
                "[PlanMode] enter_plan_mode can only be called in plan mode.",
            )

    def _handle_exit(self, ctx: AgentCallbackContext) -> None:
        """Validate mode for exit_plan_mode and pass through if OK.

        Args:
            ctx: Callback context.
        """
        agent = self._agent
        session = ctx.session
        plan_state = agent.load_state(session).plan_mode

        if plan_state.mode != "plan":
            self._reject_tool(
                ctx,
                "[PlanMode] exit_plan_mode can only be called in plan mode.",
            )

    def _reject_tool(self, ctx: AgentCallbackContext, error_msg: str) -> None:
        """Lightweight tool rejection — sets _skip_tool and injects error result.

        Args:
            ctx: Callback context to mutate.
            error_msg: Human-readable rejection reason.
        """
        tool_call = ctx.inputs.tool_call
        tool_call_id = tool_call.id if tool_call else ""
        msg = ToolMessage(content=error_msg, tool_call_id=tool_call_id)
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = {"error": error_msg}
        ctx.inputs.tool_msg = msg

    @staticmethod
    def _is_plan_file(file_path: str, plan_path: str) -> bool:
        """Check whether a given file path resolves to the plan file.

        Args:
            file_path: Path from the tool arguments.
            plan_path: Expected plan file path.

        Returns:
            ``True`` if both paths resolve to the same file.
        """
        if not plan_path or not file_path:
            return False
        try:
            return Path(file_path).resolve() == Path(plan_path).resolve()
        except (ValueError, OSError):
            return False

    def _extract_file_path(self, ctx: AgentCallbackContext) -> str:
        inputs = ctx.inputs
        if isinstance(inputs, ToolCallInputs):
            args = inputs.tool_args
        else:
            args = getattr(inputs, "tool_args", None)
        if args is None:
            args = {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                return ""
        if isinstance(args, dict):
            return str(args.get("file_path", ""))
        return ""


__all__ = ["AgentModeRail"]
