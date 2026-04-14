# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Plan mode tools: EnterPlanModeTool and ExitPlanModeTool.

These tools are registered by PlanModeRail and manage the lifecycle of the
plan file created during planning sessions.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.core.foundation.tool import Input, Tool, Output
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.prompts.sections.tools import build_tool_card

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

# ---------------------------------------------------------------------------
# Word lists for slug generation (adjective-verb-noun, aligns with Claude Code)
# ---------------------------------------------------------------------------

_ADJECTIVES = [
    "ancient", "blazing", "calm", "daring", "eager",
    "fierce", "gleaming", "happy", "icy", "jolly",
    "keen", "lively", "mighty", "noble", "open",
    "proud", "quiet", "rapid", "silent", "tall",
    "unique", "vivid", "warm", "xenial", "young", "zealous",
]

_VERBS = [
    "brewing", "crafting", "designing", "exploring", "forging",
    "gathering", "hunting", "inspiring", "joining", "keeping",
    "learning", "making", "noting", "opening", "planning",
    "questing", "reading", "seeking", "testing", "using",
    "viewing", "writing", "yielding",
]

_NOUNS = [
    "anchor", "bridge", "cloud", "delta", "ember",
    "falcon", "galaxy", "harbor", "island", "jungle",
    "kernel", "lantern", "meadow", "nexus", "orbit",
    "phoenix", "quartz", "river", "summit", "tower",
    "union", "valley", "wave", "xenon", "yacht", "zenith",
]


def generate_word_slug() -> str:
    """Generate a random ``adjective-verb-noun`` slug.

    Uses ``secrets.randbelow`` as the random source, mirroring the
    approach taken by Claude Code's ``generateWordSlug()``.

    Returns:
        A hyphenated three-word slug, e.g. ``"gleaming-brewing-phoenix"``.

    Examples:
        >>> slug = generate_word_slug()
        >>> len(slug.split("-")) == 3
        True
    """
    adj = _ADJECTIVES[secrets.randbelow(len(_ADJECTIVES))]
    verb = _VERBS[secrets.randbelow(len(_VERBS))]
    noun = _NOUNS[secrets.randbelow(len(_NOUNS))]
    return f"{adj}-{verb}-{noun}"


def resolve_plan_file_path(workspace_root: str, plan_slug: str) -> Path:
    """Derive the absolute plan file path from the workspace root and slug.

    Creates the ``.plans`` directory if it does not yet exist.

    Args:
        workspace_root: Absolute path to the workspace root directory.
        plan_slug: Short identifier for the plan (e.g. ``"gleaming-brewing-phoenix"``).

    Returns:
        Resolved ``Path`` pointing to ``<workspace_root>/.plans/<slug>.md``.

    Examples:
        >>> import tempfile, os
        >>> with tempfile.TemporaryDirectory() as d:
        ...     p = resolve_plan_file_path(d, "test-slug")
        ...     p.parent.name
        '.plans'
    """
    plans_dir = Path(workspace_root) / ".plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir / f"{plan_slug}.md"


def get_or_create_plan_slug(workspace_root: str) -> str:
    """Generate a unique slug that does not collide with existing plan files.

    Args:
        workspace_root: Absolute path to the workspace root directory.

    Returns:
        A slug whose corresponding ``.md`` file does not yet exist under
        ``<workspace_root>/.plans/``.
    """
    plans_dir = Path(workspace_root) / ".plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        slug = generate_word_slug()
        if not (plans_dir / f"{slug}.md").exists():
            return slug
    return generate_word_slug()


_ENTER_PLAN_EXISTS_MSG = {
    "en": (
        "Plan file already exists at: {plan_path}\n"
        "You can read it and make incremental edits. "
        "Continue the 5-phase Plan workflow in your instructions, initial understanding-design-review-final plan-end."
    ),
    "cn": (
        "计划文件已存在，路径：{plan_path}\n"
        "你可以阅读计划文件然后做增量修改。"
        "请按照提示词中的Plan工作流继续制定计划，初始理解-方案设计-审查-撰写计划-结束规划。\n"
    ),
}

_ENTER_PLAN_CREATED_MSG = {
    "en": (
        "Plan file created at: {plan_path}\n"
        "Continue the 5-phase Plan workflow in your instructions, initial understanding-design-review-final plan-end."
        "DO NOT edit any files except the plan file.\n"
    ),
    "cn": (
        "计划文件已创建于：{plan_path}\n"
        "请按照提示词中的Plan工作流继续制定计划，初始理解-方案设计-审查-撰写计划-结束规划。\n"
        "除计划文件外，请勿编辑任何其他文件。\n"
    ),
}

_EXIT_PLAN_EMPTY_MSG = {
    "en": (
        "Plan mode ended. You can now exit the turn.\n"
        "Plan file: {plan_path}"
    ),
    "cn": (
        "规划模式已结束。你现在可以结束本轮。\n"
        "计划文件：{plan_path}"
    ),
}

_EXIT_PLAN_WITH_CONTENT_PREFIX = {
    "en": (
        "Plan mode ended. \n"
        "Plan file: {plan_path}\n\n"
        "## Plan:\n"
    ),
    "cn": (
        "规划模式已结束。\n"
        "计划文件：{plan_path}\n\n"
        "## 计划：\n"
    ),
}


def _plan_mode_tool_language(language: str) -> str:
    """Normalize tool result language to a supported code."""
    return language if language == "en" else "cn"


class EnterPlanModeTool(Tool):
    """Create the plan file and return its path.

    Must be the first tool called when entering plan mode.  Subsequent
    calls are idempotent: if the file already exists the path is returned
    without creating a new file.
    """

    def __init__(self, agent_ref: "DeepAgent", language: str = "cn") -> None:
        """Initialize EnterPlanModeTool.

        Args:
            agent_ref: Reference to the parent DeepAgent used to access
                session state and workspace config.
            language: UI language for the tool card (``"cn"`` or ``"en"``).
        """
        super().__init__(
            build_tool_card(
                name="enter_plan_mode",
                tool_id="enter_plan_mode",
                language=language,
            )
        )
        self._agent_ref = agent_ref
        self._language = language

    async def invoke(self, inputs: Input, **kwargs) -> str:
        """Create the plan file and return its path.

        Args:
            inputs: Tool input (unused — no parameters required).
            **kwargs: Additional runtime kwargs (ignored).

        Returns:
            Human-readable result message containing the plan file path.
        """
        agent = self._agent_ref
        session = kwargs.get("session")
        state = agent.load_state(session)

        if state.plan_mode.plan_slug:
            existing_path = resolve_plan_file_path(
                agent.deep_config.workspace.root_path,
                state.plan_mode.plan_slug,
            )
            if existing_path.exists():
                lang = _plan_mode_tool_language(self._language)
                return _ENTER_PLAN_EXISTS_MSG[lang].format(plan_path=existing_path)

        workspace_root = agent.deep_config.workspace.root_path
        slug = get_or_create_plan_slug(workspace_root)
        plan_path = resolve_plan_file_path(workspace_root, slug)
        plan_path.parent.mkdir(parents=True, exist_ok=True)

        state.plan_mode.plan_slug = slug
        agent.save_state(session, state)

        lang = _plan_mode_tool_language(self._language)
        return _ENTER_PLAN_CREATED_MSG[lang].format(plan_path=plan_path)

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


class ExitPlanModeTool(Tool):
    """Read the full plan file content and return to the user.

    Ends the planning phase.  The complete plan text is included in the
    tool result so the LLM can reference it when starting execution.
    """

    def __init__(self, agent_ref: "DeepAgent", language: str = "cn") -> None:
        """Initialize ExitPlanModeTool.

        Args:
            agent_ref: Reference to the parent DeepAgent.
            language: UI language for the tool card (``"cn"`` or ``"en"``).
        """
        super().__init__(
            build_tool_card(
                name="exit_plan_mode",
                tool_id="exit_plan_mode",
                language=language,
            )
        )
        self._agent_ref = agent_ref
        self._language = language

    async def invoke(self, inputs: Input, **kwargs) -> str:
        """Read plan file and restore auto mode.

        Args:
            inputs: Tool input (unused — no parameters required).
            **kwargs: Additional runtime kwargs (ignored).

        Returns:
            Human-readable result that includes the full plan text (if any).
        """
        agent = self._agent_ref
        session = kwargs.get("session")

        plan_path = agent.get_plan_file_path(session)
        plan_text = ""
        if plan_path and plan_path.exists():
            plan_text = plan_path.read_text(encoding="utf-8")

        plan_path_str = str(plan_path) if plan_path else ""
        lang = _plan_mode_tool_language(self._language)
        if not plan_text.strip():
            return _EXIT_PLAN_EMPTY_MSG[lang].format(plan_path=plan_path_str)

        prefix = _EXIT_PLAN_WITH_CONTENT_PREFIX[lang].format(plan_path=plan_path_str)
        return prefix + plan_text

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


__all__ = [
    "EnterPlanModeTool",
    "ExitPlanModeTool",
    "generate_word_slug",
    "get_or_create_plan_slug",
    "resolve_plan_file_path",
]
