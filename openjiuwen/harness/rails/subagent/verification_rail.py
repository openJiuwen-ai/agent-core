# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""VerificationRail — tool allowlist and per-turn reminder for the verification agent.

Responsibilities:
1. ``init``: capture the system_prompt_builder reference.
2. ``before_model_call``: inject a critical reminder section so the agent
   cannot forget its constraints across long multi-step runs.
3. ``before_tool_call``: enforce the tool allowlist — block any write or
   modification tool that is not in the permitted set.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.workspace.workspace import Workspace

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

# ---------------------------------------------------------------------------
# Tool allowlist
# Tools registered by SysOperationRail but NOT in this set will be blocked.
# ---------------------------------------------------------------------------

VERIFICATION_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "bash",
    "grep",
    "glob",
    "list_files",
    "web_search",
    "web_fetch",
    "todo_create",
    "todo_list",
    "todo_modify",
    "skill_tool",
    "tool_search",
})

# Maps path-reading tool names to the tool_args key that holds the target path.
# Used by the workspace scope guard to intercept out-of-scope path requests early
# with a clear explanation rather than letting the error bubble up as a cryptic
# "Access denied: ... outside sandbox" message from the SysOperation layer.
_PATH_TOOL_ARG: dict[str, str] = {
    "list_files": "path",
    "read_file": "file_path",
    "glob": "path",
    "grep": "path",
}

# ---------------------------------------------------------------------------
# Per-turn critical reminder section
# ---------------------------------------------------------------------------

_REMINDER_SECTION_NAME = "verification_reminder"
_REMINDER_PRIORITY = 95  # injected late so it sits near the end of the assembled prompt

_REMINDER_EN = (
    "=== VERIFICATION AGENT — ACTIVE CONSTRAINTS ===\n"
    "1. You CANNOT create, modify, or delete project files. "
    "Use /tmp only for ephemeral test scripts.\n"
    "2. Every check MUST include a 'Command run' block with verbatim terminal output. "
    "A check without a command block is a SKIP, not a PASS.\n"
    "3. You MUST end your final response with exactly one of:\n"
    "   VERDICT: PASS\n"
    "   VERDICT: FAIL\n"
    "   VERDICT: PARTIAL\n"
    "   No markdown, no punctuation after the verdict word, no variation.\n"
    "4. Reading code is NOT verification. Run commands and show actual output."
)

_REMINDER_CN = """\
=== 验证代理 -- 当前约束 ===
1. 你不能创建、修改或删除项目文件。/tmp 仅可用于临时测试脚本。
2. 每项检查必须包含'执行命令'块，并逐字粘贴终端输出。没有命令块的检查视为跳过，而非 PASS。
3. 你必须以以下之一结束最终回复：
   VERDICT: PASS
   VERDICT: FAIL
   VERDICT: PARTIAL
   不加 Markdown，判决词后不加标点，不得有任何格式变体。
4. 阅读代码不等于验证。运行命令并展示实际输出。"""


class VerificationRail(DeepAgentRail):
    """Rail that enforces verification-agent constraints.

    Designed to be layered on top of :class:`SysOperationRail`.
    SysOperationRail registers all filesystem and shell tools; this rail
    then restricts which of those tools the verification agent may
    actually call, and re-injects a constraint reminder before each
    model turn so the agent cannot forget its role mid-run.

    Args:
        allowed_tools: Set of tool names the agent is permitted to call.
            Defaults to :data:`VERIFICATION_ALLOWED_TOOLS`.
    """

    priority = 90  # runs after SysOperationRail (100) so tools are already registered

    def __init__(self, allowed_tools: frozenset[str] | None = None) -> None:
        super().__init__()
        self._allowed_tools: frozenset[str] = (
            allowed_tools if allowed_tools is not None else VERIFICATION_ALLOWED_TOOLS
        )
        self.system_prompt_builder = None

    def init(self, agent: "DeepAgent") -> None:
        """Capture the system_prompt_builder for use in before_model_call.

        Args:
            agent: The parent DeepAgent being initialised.
        """
        self._agent = agent
        self.system_prompt_builder = agent.system_prompt_builder
        logger.info("[VerificationRail] Initialised with %d allowed tools", len(self._allowed_tools))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the critical reminder section before every model turn.

        Injection is skipped when the outer task loop is not active (e.g. a
        simple one-shot session) or when the agent is currently in plan mode,
        where the reminder adds noise without benefit.

        The section is replaced each call so its content stays current
        regardless of language changes mid-session.

        Args:
            ctx: Callback context providing the current session.
        """
        if self.system_prompt_builder is None:
            return

        # Only inject while the task loop is running — skip for plain sessions.
        deep_config = getattr(self._agent, "_deep_config", None)
        if deep_config is None or not deep_config.enable_task_loop:
            return

        # Skip while in plan mode — the agent is not yet executing work.
        if ctx.session is not None:
            try:
                state = self._agent.load_state(ctx.session)
                if getattr(state.plan_mode, "mode", None) == "plan":
                    return
            except Exception:  # noqa: BLE001
                pass

        language = self.system_prompt_builder.language
        reminder = PromptSection(
            name=_REMINDER_SECTION_NAME,
            content={"en": _REMINDER_EN, "cn": _REMINDER_CN},
            priority=_REMINDER_PRIORITY,
        )
        self.system_prompt_builder.remove_section(_REMINDER_SECTION_NAME)
        self.system_prompt_builder.add_section(reminder)
        logger.debug("[VerificationRail] Injected critical reminder section (lang=%s)", language)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Block disallowed tools and enforce workspace scope for path-reading tools.

        Two guards run in order:
        1. Allowlist — rejects any tool not in VERIFICATION_ALLOWED_TOOLS.
        2. Workspace scope — for tools that accept a filesystem path, rejects
           calls where the resolved path falls outside the configured workspace
           root with a clear explanation.  This fires before the SysOperation
           layer so the agent sees a readable message instead of a cryptic
           "Access denied: outside sandbox" error.

        Args:
            ctx: Callback context containing the tool name and call metadata.
        """
        if ctx.extra.get("_skip_tool"):
            return

        tool_name = ctx.inputs.tool_name

        # MCP tools (mcp__*) pass through unconditionally — same convention
        # used elsewhere in the harness for MCP tool access.
        if tool_name and tool_name.startswith("mcp__"):
            return

        if tool_name not in self._allowed_tools:
            error_msg = (
                f"[VerificationAgent] Tool '{tool_name}' is not available to the verification agent. "
                f"Permitted tools: {', '.join(sorted(self._allowed_tools))}."
            )
            logger.info("[VerificationRail] Blocked tool '%s'", tool_name)
            self._reject_tool(ctx, error_msg)
            return

        # Workspace scope guard for path-reading tools.
        path_arg_key = _PATH_TOOL_ARG.get(tool_name)
        if path_arg_key and self.workspace is not None:
            tool_args = ctx.inputs.tool_args
            # tool_args arrives as a raw JSON string from ToolCall.arguments before
            # the ability manager parses it; parse here so we can extract path args.
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except Exception:  # noqa: BLE001
                    tool_args = {}
            raw_path = tool_args.get(path_arg_key) if isinstance(tool_args, dict) else None
            if raw_path:
                workspace_root = (
                    self.workspace.root_path
                    if isinstance(self.workspace, Workspace)
                    else self.workspace
                )
                try:
                    resolved = Path(raw_path).expanduser().resolve()
                    root = Path(workspace_root).expanduser().resolve()
                    if not (resolved == root or resolved.is_relative_to(root)):
                        error_msg = (
                            f"[VerificationAgent] Path '{raw_path}' is outside the workspace scope "
                            f"(workspace root: '{root}'). "
                            f"Only paths within the workspace are accessible. "
                            f"Use paths relative to '{root}' or absolute paths within it."
                        )
                        logger.info(
                            "[VerificationRail] Blocked out-of-scope path '%s' for tool '%s' "
                            "(workspace root: '%s')",
                            raw_path, tool_name, root,
                        )
                        self._reject_tool(ctx, error_msg)
                except Exception:  # noqa: BLE001
                    pass  # Unresolvable paths fall through to the tool layer.

    def _reject_tool(self, ctx: AgentCallbackContext, error_msg: str) -> None:
        """Mark a tool call as skipped and inject an error result.

        Args:
            ctx: Callback context to mutate.
            error_msg: Human-readable rejection reason returned to the model.
        """
        tool_call = ctx.inputs.tool_call
        tool_call_id = tool_call.id if tool_call else ""
        msg = ToolMessage(content=error_msg, tool_call_id=tool_call_id)
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = {"error": error_msg}
        ctx.inputs.tool_msg = msg


__all__ = [
    "VERIFICATION_ALLOWED_TOOLS",
    "VerificationRail",
]
