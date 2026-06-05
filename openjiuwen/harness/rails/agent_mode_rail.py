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
import re
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.agent_mode import build_plan_mode_section
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools import SwitchModeTool, EnterPlanModeTool, ExitPlanModeTool, create_task_tool

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

# ---------------------------------------------------------------------------
# Tool name sets
# ---------------------------------------------------------------------------

_TODO_TOOL_NAMES = frozenset({"todo_create", "todo_list", "todo_modify"})
_SESSION_TOOL_NAMES = frozenset({"sessions_list", "sessions_cancel", "sessions_spawn"})
_HIDDEN_IN_PLAN = _TODO_TOOL_NAMES | _SESSION_TOOL_NAMES
_HIDDEN_IN_NORMAL = frozenset({"enter_plan_mode", "exit_plan_mode"})

_PLAN_FILE_WRITE_TOOLS = frozenset({"write_file", "edit_file"})

# Git write operations that must be blocked in plan mode.
# Read-only git commands (status, log, diff, branch, remote, etc.) are allowed.
_GIT_WRITE_RE = re.compile(
    r"\bgit\s+(add|commit|push|pull|reset\s+--hard|checkout\s+--\.|clean\s+-[a-zA-Z]*f|"
    r"stash\s+(drop|clear)|branch\s+-D|merge|tag|amend|rebase)\b"
)

# Non-git write operations to block in plan mode's bash tool.
# Covers common shell write commands: mkdir, touch, rm, mv, cp, chmod,
# chown, dd, tee, wget/curl with output flags, archivers (tar, zip, 7z),
# and output redirection (>, >>).
_NON_GIT_WRITE_RE = re.compile(
    r"\b(mkdir|touch|rm|mv|cp|chmod|chown|dd|tee|wget|curl\s+.*\s*-[a-zA-Z]*O)\b"
    r"|\b(7z|tar|zip|unzip|gzip|gunzip)\s+"
    r"|>>"
    r"|(?<![=<>])\s>\s*[^\s=]"
)

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
    TaskPlanningRail(90) / SubagentRail(95) so it can remove sections
    those rails added.

    Args:
        allowed_tools: Whitelist of tool names permitted in plan mode.
            If ``None``, uses :data:`DEFAULT_PLAN_MODE_ALLOWED_TOOLS`.
        allow_switch_mode: When False, ``switch_mode`` is excluded from the
            plan-mode whitelist so the LLM cannot unilaterally exit plan mode.
            Defaults to True for backward compatibility.
        plan_mode_system_note: Optional static system prompt note for plan
            mode. When provided, injected into MODE_INSTRUCTIONS as a static
            section (KV-cache-friendly) instead of the dynamic
            ``build_plan_mode_section()`` output. When None, existing dynamic
            behavior is used. Defaults to None.
        enter_plan_instructions: Optional instructions appended to
            ``enter_plan_mode`` tool_result. Aligns with Claude Code behavior
            where plan instructions live in conversation, not system prompt.
            When None, no instructions are appended. Defaults to None.
        exit_plan_notification: Optional notification appended to
            ``exit_plan_mode`` tool_result. Explicitly signals the model that
            write operations are now permitted. When None, no notification is
            appended. Defaults to None.
    """

    priority = 85

    def __init__(
        self,
        allowed_tools: list[str] | None = None,
        *,
        allow_switch_mode: bool = True,
        plan_mode_system_note: str | None = None,
        enter_plan_instructions: str | None = None,
        exit_plan_notification: str | None = None,
    ) -> None:
        super().__init__()
        names = (
            allowed_tools
            if allowed_tools is not None
            else list(DEFAULT_PLAN_MODE_ALLOWED_TOOLS)
        )
        if not allow_switch_mode:
            names = [t for t in names if t != "switch_mode"]
        self._allowed_tools: frozenset[str] = frozenset(names)
        self._allow_switch_mode: bool = allow_switch_mode
        self._plan_mode_system_note: str | None = plan_mode_system_note
        self._enter_plan_instructions: str | None = enter_plan_instructions
        self._exit_plan_notification: str | None = exit_plan_notification
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
            agent.ability_manager.add_ability(tool.card, tool)

        logger.info("[AgentModeRail] Registered enter/exit plan mode tools")

        # Patch EnterPlanModeTool / ExitPlanModeTool to append extra content
        # when configured (aligns with Claude Code: plan instructions live in
        # conversation via tool_result, not in system prompt).
        self._patch_enter_plan_mode_tool()
        self._patch_exit_plan_mode_tool()

    def _patch_enter_plan_mode_tool(self) -> None:
        """Patch EnterPlanModeTool.invoke() to append plan instructions.

        When ``_enter_plan_instructions`` is configured, the full plan mode
        workflow instructions are returned in the tool_result, matching
        Claude Code behavior where plan instructions live in conversation
        rather than in the system prompt.
        """
        instructions = self._enter_plan_instructions
        if not instructions:
            return

        for tool in self._tools:
            if getattr(tool.card, "name", "") != "enter_plan_mode":
                continue

            original_invoke = tool.invoke

            async def patched_invoke(inputs, _orig=original_invoke, **kwargs):
                result = await _orig(inputs, **kwargs)
                return result + "\n\n" + instructions

            tool.invoke = patched_invoke
            logger.info(
                "[AgentModeRail] Patched enter_plan_mode.invoke() "
                "to return full plan instructions in tool_result"
            )
            break

    def _patch_exit_plan_mode_tool(self) -> None:
        """Patch ExitPlanModeTool.invoke() to append exit notification.

        When ``_exit_plan_notification`` is configured, an explicit
        notification is appended to the tool_result so the model knows
        write operations are now permitted. Without this, the model only
        sees MODE_INSTRUCTIONS removed from the system prompt but receives
        no explicit signal.
        """
        notification = self._exit_plan_notification
        if not notification:
            return

        for tool in self._tools:
            if getattr(tool.card, "name", "") != "exit_plan_mode":
                continue

            original_invoke = tool.invoke

            async def patched_invoke(inputs, _orig=original_invoke, **kwargs):
                result = await _orig(inputs, **kwargs)
                return result + "\n\n" + notification

            tool.invoke = patched_invoke
            logger.info(
                "[AgentModeRail] Patched exit_plan_mode.invoke() "
                "to append plan mode exit notification in tool_result"
            )
            break

    def _language_is_cn(self) -> bool:
        """True when UI/messages should use Simplified Chinese."""
        if not self.system_prompt_builder:
            return True
        return self.system_prompt_builder.language == "cn"

    def uninit(self, agent: "DeepAgent") -> None:
        """Unregister all tools owned by this rail.

        Args:
            agent: The parent DeepAgent being torn down.
        """
        for tool in self._tools:
            try:
                agent.ability_manager.remove_ability(tool.card.name)
            except Exception as exc:
                logger.warning(
                    f"[AgentModeRail] Failed to remove tool '{tool.name}': {exc}"
                )
        self._tools = []

        if self._owns_task_tool and self._task_tools:
            self._unregister_task_tool(agent)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject MODE_INSTRUCTIONS and filter hidden tools when in plan mode.

        Two strategies for MODE_INSTRUCTIONS injection:

        * **Static** (``_plan_mode_system_note is not None``): injects a
          fixed-content section whose content never changes across turns,
          keeping the KV cache stable.  Uses whitelist-based tool filtering.
        * **Dynamic** (``_plan_mode_system_note is None``, default): calls
          ``build_plan_mode_section()`` with current plan-file state.
          Uses blacklist-based tool filtering.

        In normal mode, defensively removes AgentModeRail-owned ``task_tool``
        from the visible tool list — this covers the edge case where
        ``switch_mode`` exits plan mode without going through
        ``exit_plan_mode`` (which would have called ``_unregister_task_tool``).

        Args:
            ctx: Callback context providing agent, session, and inputs.
        """
        agent = self._agent
        session = ctx.session
        plan_state = agent.load_state(session).plan_mode

        if plan_state.mode != "plan":
            # ---- normal mode ----
            self.system_prompt_builder.remove_section(SectionName.MODE_INSTRUCTIONS)
            self._sync_task_tool_for_model_tool_inputs(ctx)
            if isinstance(ctx.inputs.tools, list):
                ctx.inputs.tools = [
                    t for t in ctx.inputs.tools
                    if getattr(t, "name", "") not in _HIDDEN_IN_NORMAL
                ]

            # Defensive cleanup: when switch_mode bypasses exit_plan_mode,
            # _owns_task_tool is still True and _sync_task_tool will inject
            # task_tool into the tool list.  Remove it when not in plan mode.
            # Only filter when _owns_task_tool is True to avoid removing
            # SubagentRail's task_tool (registered unconditionally on init).
            if (
                self._owns_task_tool
                and isinstance(ctx.inputs.tools, list)
            ):
                ctx.inputs.tools = [
                    t for t in ctx.inputs.tools
                    if getattr(t, "name", "") != "task_tool"
                ]
            return

        # ---- plan mode ----
        if self._plan_mode_system_note is not None:
            # Static path: KV-cache-friendly fixed content
            section = PromptSection(
                name=SectionName.MODE_INSTRUCTIONS,
                content={"en": self._plan_mode_system_note},
                priority=85,
            )
        else:
            # Dynamic path: build from current plan-file state
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

        # 1. Inject MODE_INSTRUCTIONS
        self.system_prompt_builder.add_section(section)

        # 2. Remove Todo/Session sections added by higher-priority rails
        self.system_prompt_builder.remove_section(SectionName.TODO)
        self.system_prompt_builder.remove_section(SectionName.SESSION_TOOLS)

        # 3. Filter hidden tools from the visible tool list
        if isinstance(ctx.inputs.tools, list):
            if self._plan_mode_system_note is not None:
                # Static path: whitelist-based filtering (more secure —
                # the model never sees tools it cannot use)
                filtered = []
                for t in ctx.inputs.tools:
                    tool_name = getattr(t, "name", "")
                    if tool_name in self._allowed_tools:
                        if (tool_name != "switch_mode" or self._allow_switch_mode) and tool_name not in _HIDDEN_IN_PLAN:
                            filtered.append(t)
                ctx.inputs.tools = filtered
            else:
                # Dynamic path: blacklist-based filtering (backward compat)
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
            if self._language_is_cn():
                hidden_msg = f"[AgentModeRail] 工具「{tool_name}」在 plan 模式下已隐藏。"
            else:
                hidden_msg = (
                    f"[AgentModeRail] Tool '{tool_name}' is hidden in plan mode."
                )
            self._reject_tool(ctx, hidden_msg)
            return

        # 3b. Not in whitelist → reject
        if self._allowed_tools and tool_name not in self._allowed_tools:
            logger.info("reject tool call by not in allowed tools")
            if self._language_is_cn():
                allow_msg = (
                    f"[AgentModeRail] 工具「{tool_name}」不在 plan 模式允许列表中。"
                )
            else:
                allow_msg = (
                    f"[AgentModeRail] Tool '{tool_name}' is not available in plan mode."
                )
            self._reject_tool(ctx, allow_msg)
            return

        # 3c. bash → block git write operations
        if tool_name == "bash":
            command = self._extract_bash_command(ctx)
            if _GIT_WRITE_RE.search(command):
                logger.info("reject bash call: git write operation in plan mode")
                if self._language_is_cn():
                    git_msg = (
                        f"[AgentModeRail] plan 模式下禁止执行 git 写操作（{command!r}）。"
                    )
                else:
                    git_msg = (
                        f"[AgentModeRail] Git write operations are blocked in plan mode "
                        f"({command!r})."
                    )
                self._reject_tool(ctx, git_msg)
                return

            # Also block non-git write operations (mkdir, touch, rm, mv, cp,
            # chmod, chown, dd, tee, wget/curl -O, archivers, redirection).
            # The _GIT_WRITE_RE above only covers git subcommands; this
            # catches common shell write patterns.
            if _NON_GIT_WRITE_RE.search(command):
                logger.info(
                    "reject bash call: non-git write operation in plan mode"
                )
                if self._language_is_cn():
                    bash_msg = (
                        f"[AgentModeRail] plan 模式下禁止执行写操作（{command!r}）。"
                    )
                else:
                    bash_msg = (
                        f"[AgentModeRail] Write operations are blocked in "
                        f"plan mode ({command!r})."
                    )
                self._reject_tool(ctx, bash_msg)
                return

        # 3d. write_file / edit_file → must target plan file only
        if tool_name in _PLAN_FILE_WRITE_TOOLS:
            file_path = self._extract_file_path(ctx)
            plan_path = agent.get_plan_file_path(session)
            plan_path_str = str(plan_path) if plan_path else ""
            if not self._is_plan_file(file_path, plan_path_str):
                logger.info("reject tool call by not in plan file")
                if self._language_is_cn():
                    plan_file_msg = (
                        f"[AgentModeRail] 「{tool_name}」仅能用于计划文件（{plan_path_str}）。"
                    )
                else:
                    plan_file_msg = (
                        f"[AgentModeRail] '{tool_name}' can only target the plan file "
                        f"({plan_path_str})."
                    )
                self._reject_tool(ctx, plan_file_msg)
                return

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Dynamically register/unregister task_tool around enter/exit.

        Also supplements mode restoration when ``exit_plan_mode`` does not
        call ``restore_mode_after_plan_exit`` (plan content empty, or a hook
        blocked the tool execution).  Restoration runs only while
        ``plan_mode.mode`` is still ``"plan"``, which avoids false triggers
        when the call was rejected outside plan mode and prevents double
        restoration after a successful exit with plan content.

        Args:
            ctx: Callback context.
        """
        tool_name = ctx.inputs.tool_name
        agent = self._agent

        if tool_name == "enter_plan_mode" and not ctx.extra.get("_skip_tool"):
            self._register_task_tool(agent)

        elif tool_name == "exit_plan_mode" and not ctx.extra.get("_skip_tool"):
            self._unregister_task_tool(agent)

        # Supplement mode restoration: when exit_plan_mode with empty plan
        # content exits early without calling restore_mode_after_plan_exit,
        # mode is still "plan".  Also covers the case where a hook blocks
        # tool execution (_skip_tool=True but restore was never called).
        #
        # IMPORTANT: when exit_plan_mode is intercepted by a higher-priority
        # rail (e.g. ConfirmInterruptRail raises AbortError), tool_result is
        # None and the tool has NOT executed — we must NOT restore the mode
        # yet.  The user may still approve the call, in which case the tool
        # will execute and restore the mode itself.
        if tool_name == "exit_plan_mode":
            session = ctx.session
            state = agent.load_state(session)
            if (state.plan_mode.mode == "plan"
                    and ctx.inputs.tool_result is not None):
                try:
                    agent.restore_mode_after_plan_exit(session)
                    logger.info(
                        "[AgentModeRail] Restored mode after plan exit "
                        "(plan was empty or hook blocked execution, "
                        "tool did not restore)"
                    )
                except Exception as exc:
                    logger.warning(
                        "[AgentModeRail] Failed to restore mode: %s", exc
                    )

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
        for tool in self._task_tools:
            agent.ability_manager.add_ability(tool.card, tool)
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
                agent.ability_manager.remove_ability(tool.card.name)
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
            if self._language_is_cn():
                msg = (
                    "[AgentModeRail] enter_plan_mode 只能在 plan 模式下被调用。"
                    "请调用 switch_mode 工具切换到 plan 模式。"
                )
            else:
                msg = (
                    "[AgentModeRail] enter_plan_mode can only be called in plan mode. "
                    "Use the switch_mode tool to switch to plan mode."
                )
            self._reject_tool(ctx, msg)

    def _handle_exit(self, ctx: AgentCallbackContext) -> None:
        """Validate mode for exit_plan_mode and pass through if OK.

        Args:
            ctx: Callback context.
        """
        agent = self._agent
        session = ctx.session
        plan_state = agent.load_state(session).plan_mode

        if plan_state.mode != "plan":
            if self._language_is_cn():
                msg = "[AgentModeRail] exit_plan_mode 只能在 plan 模式下被调用。"
            else:
                msg = "[AgentModeRail] exit_plan_mode can only be called in plan mode."
            self._reject_tool(ctx, msg)

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

    def _extract_bash_command(self, ctx: AgentCallbackContext) -> str:
        """Extract the shell command string from a bash tool invocation.

        Args:
            ctx: Callback context.

        Returns:
            The command string, or empty string if not extractable.
        """
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
            return str(args.get("command", ""))
        return ""


__all__ = ["AgentModeRail"]
