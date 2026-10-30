# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for the cua (computer-use) desktop subagent."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ToolResultWindowProcessorConfig
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.harness.rails.multimodal_context_summarizer_rail import MultimodalContextSummarizerRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools.cua.cua_capabilities import (
    DEFAULT_CUA_AGENT_CAPABILITY_NAMES,
    DEFAULT_CUA_CAPABILITIES,
    resolve_cua_capabilities,
)
from openjiuwen.harness.tools.cua.desktop_tools import CuaDesktop, build_cua_desktop_tools
from openjiuwen.harness.tools.cua.rails import (
    CuaProgressRail,
    CuaRepeatFailureRail,
    CuaRuntimeRail,
    CuaScreenshotDownscaleRail,
    CuaUserTakeoverRail,
)

try:
    from openjiuwen.harness.prompts import resolve_language
except ImportError:

    def resolve_language(language: Optional[str] = None) -> str:  # type: ignore[misc]
        return language if language in {"cn", "en"} else "cn"


if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace


CUA_AGENT_FACTORY_NAME = "cua_agent"

# The cua tools are registered under their bare names, so the rails match them
# with an empty prefix.
_CUA_TOOL_PREFIX = ""

# Characters of a superseded capture left inline after it is offloaded. The
# placeholder also carries the offload handle, so the full result stays
# reachable; the window header is all an old capture is still good for.
_CUA_OFFLOAD_PREVIEW_CHARS = 200

_CUA_PROMPT_EN = (
    "You are a desktop automation agent that operates the host computer through screenshots and real "
    "mouse and keyboard input. Plan and decide at this agent level, then observe and act on real "
    "application windows. "
    "Perception: start with list_windows (or list_apps) to find the target pid and window_id, then call "
    "get_window_state(pid, window_id): it brings that window to the front and returns its screenshot. "
    "Every x,y you pass to click, double_click, right_click, move_cursor, drag, scroll or type_text for "
    "that window is a pixel in its latest screenshot, measured from the screenshot's top-left corner. "
    "Use zoom to read small text or icons, then pass from_zoom=true with x,y read off the zoom image. "
    "A window whose x,y is near -32000 is minimized; bring_to_front before capturing it. "
    "When you act by x,y coordinates read off a screenshot, treat the target region's edges as "
    "unreliable: your visual estimate of a boundary can be off by tens of pixels, and input that "
    "starts outside the real region usually fails silently. Start clicks and drags well inside "
    "the region (30+ px from every estimated edge), and read a silent no-effect result as a "
    "likely aim miss. "
    "Your input is real: it moves the user's mouse and types into whatever has keyboard focus, so "
    "every window-scoped action brings its window to the front first. Do not touch windows the task "
    "does not need. "
    "Verification: input actions are not self-verifying. After clicks, keys, or typed text, capture "
    "the window again with get_window_state and check the effect before claiming progress. "
    "Prefer launch_app to start applications. "
    "Browser tasks are not yours: web page automation belongs to the browser agent — report back "
    "instead of driving a browser through desktop input. This means an actual web browser such as "
    "Chrome, Edge, or Firefox. Electron desktop applications — Discord, Slack, VS Code, Spotify "
    "and the like — are ordinary native application windows even though Chromium renders them, "
    "and driving them IS your job; do not hand them to the browser agent, which cannot reach "
    "them. "
    "Avoid redundant actions, and only claim completion when the requested desktop outcome is "
    "actually evidenced on screen."
)

_CUA_PROMPT_CN = (
    "你是桌面自动化代理，通过截图和真实的鼠标键盘输入操作本机计算机。"
    "请在当前代理层面规划和决策，然后基于真实应用窗口进行观察和操作。"
    "感知：先用 list_windows（或 list_apps）找到目标 pid 和 window_id，"
    "再调用 get_window_state(pid, window_id)：它会把该窗口置于前台并返回其截图。"
    "你对该窗口调用 click、double_click、right_click、move_cursor、drag、scroll 或 type_text 时"
    "传入的每个 x,y 都是其最新截图中的像素，从截图左上角起算。"
    "需要看清小字或图标时用 zoom，之后传 from_zoom=true 并使用从缩放图上读取的 x,y。"
    "x,y 接近 -32000 的窗口是被最小化了；截图前先调用 bring_to_front。"
    "通过截图读取 x,y 坐标操作时，把目标区域的边缘当作不可靠信息：你对边界的视觉估计"
    "可能偏差数十像素，而起点落在真实区域之外的输入通常会静默失效。"
    "点击和拖拽的起点应落在区域内部足够深处（离每条估计边缘至少 30 像素）；"
    "操作后毫无效果时，优先怀疑是瞄准偏差。"
    "你的输入是真实的：它会移动用户的鼠标，并向当前拥有键盘焦点的位置输入，"
    "所以每个针对窗口的操作都会先把该窗口置于前台。不要触碰任务不需要的窗口。"
    "验证：输入类操作不会自我验证。点击、按键或输入文本后，"
    "必须再次调用 get_window_state 截图并确认效果，然后才能声明进展。"
    "启动应用优先使用 launch_app。"
    "浏览器任务不属于你：网页自动化由浏览器代理负责——遇到此类任务应如实汇报，"
    "而不是通过桌面输入去驱动浏览器。这里指的是真正的浏览器，例如 Chrome、Edge、Firefox。"
    "Discord、Slack、VS Code、Spotify 等 Electron 桌面应用虽然由 Chromium 渲染，"
    "但它们就是普通的原生应用窗口，操作它们正是你的职责；"
    "不要把它们交给浏览器代理，浏览器代理无法访问这些窗口。"
    "避免重复动作；只有屏幕上有具体证据证明任务完成时，才声明完成。"
)

DEFAULT_CUA_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": _CUA_PROMPT_CN,
    "en": _CUA_PROMPT_EN,
}


# Appended only when the app_termination capability grants kill_app, so the
# default prompt never teaches a tool the agent was not given.
CUA_KILL_APP_PROMPT_SUFFIX: Dict[str, str] = {
    "cn": "kill_app 只在协作式关闭失败后使用，因为未保存状态会丢失。",
    "en": " Use kill_app only after the cooperative close path failed, since unsaved state is lost.",
}


def _default_system_prompt(language: str, allowed_tool_names: Iterable[str] = ()) -> str:
    prompt = DEFAULT_CUA_AGENT_SYSTEM_PROMPT.get(language, DEFAULT_CUA_AGENT_SYSTEM_PROMPT["cn"])
    if "kill_app" in allowed_tool_names:
        prompt += CUA_KILL_APP_PROMPT_SUFFIX.get(language, CUA_KILL_APP_PROMPT_SUFFIX["cn"])
    return prompt


# Desktop rules merged into every cua agent's own permission policy. They are
# not package builtin rules: the tool names are bare, and another agent's
# same-named tool (mobile_gui's type_text) must not inherit them. As user-layer
# rules a DENY still beats any caller allow, default or approval override.
CUA_PERMISSION_RULES: tuple[dict, ...] = (
    {
        "id": "cua_desktop_force_terminate_app",
        "description": "强制终止桌面进程（kill_app），未保存的数据会丢失",
        "tools": ["kill_app"],
        "match_type": "os_control",
        "pattern": "*",
        "severity": "CRITICAL",
        "action": "deny",
    },
    {
        "id": "cua_desktop_launch_app",
        "description": "启动桌面应用程序（launch_app），可能运行任意本机程序",
        "tools": ["launch_app"],
        "match_type": "os_control",
        "pattern": "*",
        "severity": "HIGH",
        "action": "ask",
    },
    {
        "id": "cua_desktop_type_text",
        "description": "向桌面窗口注入文本输入（type_text），可能写入密码或其他敏感字段",
        "tools": ["type_text"],
        "match_type": "os_control",
        "pattern": "*",
        "severity": "HIGH",
        "action": "ask",
    },
)


def _with_cua_permission_rules(permissions: dict) -> dict:
    """The caller's policy plus the desktop rules; the caller's dict is left untouched."""
    merged = copy.deepcopy(dict(permissions))
    rules = merged.get("rules")
    merged["rules"] = (list(rules) if isinstance(rules, list) else []) + copy.deepcopy(list(CUA_PERMISSION_RULES))
    return merged


DEFAULT_CUA_AGENT_DESCRIPTION_EN = (
    "Dedicated desktop subagent that controls host applications through screenshots and real mouse and "
    "keyboard input; requires a vision-capable model."
)
DEFAULT_CUA_AGENT_DESCRIPTION_CN = (
    "专用桌面子代理，通过截图和真实的鼠标键盘输入操作本机应用程序；需要具备视觉能力的模型。"
)
DEFAULT_CUA_AGENT_DESCRIPTION: Dict[str, str] = {
    "cn": DEFAULT_CUA_AGENT_DESCRIPTION_CN,
    "en": DEFAULT_CUA_AGENT_DESCRIPTION_EN,
}


def _resolve_capability_selection(cua_capabilities: Optional[List[str]]):
    """Validate the capability selection against the trusted catalog."""
    if cua_capabilities is not None and (
        not isinstance(cua_capabilities, list)
        or not all(isinstance(capability, str) for capability in cua_capabilities)
    ):
        raise ValueError("cua_capabilities must be a list of strings")

    requested = cua_capabilities if cua_capabilities is not None else list(DEFAULT_CUA_AGENT_CAPABILITY_NAMES)
    resolved = resolve_cua_capabilities(requested)
    if resolved.rejected_names:
        rejected = ", ".join(resolved.rejected_names)
        available = ", ".join(capability.name for capability in DEFAULT_CUA_CAPABILITIES)
        raise ValueError(f"Unsupported cua capabilities: {rejected}. Available capabilities: {available}")
    return resolved


def _validate_snapshot_keep_last_k(cua_snapshot_keep_last_k: int) -> None:
    """Reject an unusable retention count at config time, not at first tool call."""
    if (
        not isinstance(cua_snapshot_keep_last_k, int)
        or isinstance(cua_snapshot_keep_last_k, bool)
        or cua_snapshot_keep_last_k < 1
    ):
        raise ValueError(f"cua_snapshot_keep_last_k must be an int >= 1, got {cua_snapshot_keep_last_k!r}")


def build_cua_agent_config(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 25,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    cua_capabilities: Optional[List[str]] = None,
    cua_instance_key: str = "",
    cua_snapshot_keep_last_k: int = 3,
    cua_pause_on_user_input: bool = False,
    permissions: Optional[dict] = None,
    permission_host: Optional[Any] = None,
) -> SubAgentConfig:
    """Build a SubAgentConfig that materializes as create_cua_agent().

    ``permissions`` / ``permission_host`` (optional) enable the tool-permission
    engine on the materialized subagent. Inside a task-tool delegation the
    built-in ASK interrupt cannot reach the outer conversation, so pair any
    policy that can produce ASK with a
    ``ToolPermissionHost.request_permission_confirmation`` hook that answers
    in-process; DENY rules need no host.
    """
    resolved_language = resolve_language(language)
    resolved_capabilities = _resolve_capability_selection(cua_capabilities)
    _validate_snapshot_keep_last_k(cua_snapshot_keep_last_k)
    return SubAgentConfig(
        agent_card=card
        or AgentCard(
            name="cua_agent",
            description=DEFAULT_CUA_AGENT_DESCRIPTION.get(
                resolved_language,
                DEFAULT_CUA_AGENT_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=system_prompt
        or _default_system_prompt(resolved_language, resolved_capabilities.allowed_tool_names),
        tools=list(tools or []),
        mcps=list(mcps or []),
        model=model,
        rails=rails,
        skills=skills,
        backend=backend,
        workspace=workspace,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        factory_name=CUA_AGENT_FACTORY_NAME,
        factory_kwargs={
            "cua_capabilities": list(cua_capabilities) if cua_capabilities is not None else None,
            "cua_instance_key": cua_instance_key,
            "cua_snapshot_keep_last_k": cua_snapshot_keep_last_k,
            "cua_pause_on_user_input": bool(cua_pause_on_user_input),
            # Only present when provided so older configs keep their exact
            # factory_kwargs shape; DeepAgentConfig owns the defaults.
            **({"permissions": permissions} if permissions is not None else {}),
            **({"permission_host": permission_host} if permission_host is not None else {}),
        },
    )


def create_cua_agent(
    model: Model,
    *,
    card: Optional[AgentCard] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Tool | ToolCard]] = None,
    mcps: Optional[List[McpServerConfig]] = None,
    subagents: Optional[List[SubAgentConfig | DeepAgent]] = None,
    rails: Optional[List[AgentRail]] = None,
    enable_task_loop: bool = False,
    max_iterations: int = 25,
    workspace: Optional[str | "Workspace"] = None,
    skills: Optional[List[str]] = None,
    backend: Optional[Any] = None,
    sys_operation: Optional[SysOperation] = None,
    language: Optional[str] = None,
    prompt_mode: Optional[str] = None,
    cua_capabilities: Optional[List[str]] = None,
    cua_instance_key: str = "",
    cua_snapshot_keep_last_k: int = 3,
    cua_pause_on_user_input: bool = False,
    **config_kwargs: Any,
) -> DeepAgent:
    """Create the cua desktop subagent with a task-scoped tool allowlist.

    The agent perceives through window screenshots and acts with real mouse
    and keyboard input, so ``model`` must be vision-capable. Only the tools of
    the resolved capabilities are built: ``cua_capabilities`` is resolved
    against the trusted capability catalog, and when omitted the default grant
    (input + app_lifecycle on top of the always-included read-only core)
    applies. :class:`CuaRuntimeRail` fails the first invoke loudly when this
    host has no usable desktop.

    ``permissions`` (via ``config_kwargs``), when given, gets
    :data:`CUA_PERMISSION_RULES` merged into its ``rules``: kill_app is
    denied whatever else the policy allows, and launch_app / type_text ask.
    Without ``permissions`` the agent runs without a permission engine.

    ``cua_instance_key`` is accepted for API stability and has no effect: every
    agent gets its own tool instances, registered under ids qualified by its
    card id.

    ``cua_pause_on_user_input`` installs :class:`CuaUserTakeoverRail`: desktop
    actions are held while the user is using the machine (mouse/keyboard input
    seen by the OS) and released once it has been idle again; a takeover that
    outlasts the rail's wait budget ends the delegation with a report. Off by
    default for now (opt in per agent); inert on hosts without a user-activity
    probe.

    ``cua_snapshot_keep_last_k`` is how many recent captures stay in context:
    it drives both the tool-result window on the capture tools and the number
    of retained screenshot images. Tasks that juggle two windows need both
    windows' latest frames in reach at once, so the default is 3; pass 1 for
    single-window tasks to spend the least context on perception history.
    """
    resolved_capabilities = _resolve_capability_selection(cua_capabilities)
    _validate_snapshot_keep_last_k(cua_snapshot_keep_last_k)

    logger.info(
        "Resolved cua capabilities: requested=%s, selected=%s, allowed_tools=%s",
        resolved_capabilities.requested_names,
        resolved_capabilities.selected_names,
        resolved_capabilities.allowed_tool_names,
    )

    if config_kwargs.get("permissions") is not None:
        config_kwargs["permissions"] = _with_cua_permission_rules(config_kwargs["permissions"])

    resolved_language = resolve_language(language)
    desktop = CuaDesktop()
    cua_tools = build_cua_desktop_tools(desktop, names=resolved_capabilities.allowed_tool_names)

    final_card = card or AgentCard(
        name="cua_agent",
        description=DEFAULT_CUA_AGENT_DESCRIPTION.get(
            resolved_language,
            DEFAULT_CUA_AGENT_DESCRIPTION["cn"],
        ),
    )
    final_prompt = system_prompt or _default_system_prompt(resolved_language, resolved_capabilities.allowed_tool_names)

    injected_rails: List[AgentRail] = [
        CuaRuntimeRail(desktop),
        CuaRepeatFailureRail(_CUA_TOOL_PREFIX),
        CuaProgressRail(_CUA_TOOL_PREFIX),
        CuaScreenshotDownscaleRail(_CUA_TOOL_PREFIX),
        # Screenshot UserMessages are not ToolMessages, so the window processor
        # below cannot evict them; retention rides the same knob so that a
        # two-window task keeps both windows' screens in reach, matching the
        # tool-result window below.
        MultimodalContextSummarizerRail(cua_snapshot_keep_last_k),
    ]

    if cua_pause_on_user_input:
        takeover_rail = CuaUserTakeoverRail(_CUA_TOOL_PREFIX)
        if not takeover_rail.active:
            logger.info("cua user-takeover pause requested but no user-activity probe exists on this host")
        injected_rails.append(takeover_rail)

    # Window the capture results unless the caller already manages context
    # processors via their own ContextProcessorRail: an old capture describes
    # a screen that has since changed, so it is persisted to the workspace
    # offload directory and replaced in context by a preview placeholder.
    # Retention defaults to 3, not 1: a task that works across two windows
    # needs both windows' latest captures in context at once.
    if not any(isinstance(rail, ContextProcessorRail) for rail in (rails or [])):
        injected_rails.append(
            ContextProcessorRail(
                processors=[
                    (
                        "ToolResultWindowProcessor",
                        ToolResultWindowProcessorConfig(
                            tool_names=["get_window_state", "zoom"],
                            keep_last_k=cua_snapshot_keep_last_k,
                            trim_size=_CUA_OFFLOAD_PREVIEW_CHARS,
                        ),
                    )
                ],
                preset=False,
            )
        )
    final_rails = list(rails or []) + injected_rails

    return create_deep_agent(
        model=model,
        card=final_card,
        system_prompt=final_prompt,
        tools=list(tools or []) + cua_tools,
        mcps=list(mcps or []),
        subagents=subagents,
        rails=final_rails,
        enable_task_loop=enable_task_loop,
        max_iterations=max_iterations,
        workspace=workspace,
        skills=skills,
        backend=backend,
        sys_operation=sys_operation,
        language=resolved_language,
        prompt_mode=prompt_mode,
        **config_kwargs,
    )


__all__ = [
    "CUA_AGENT_FACTORY_NAME",
    "CUA_PERMISSION_RULES",
    "build_cua_agent_config",
    "create_cua_agent",
]
