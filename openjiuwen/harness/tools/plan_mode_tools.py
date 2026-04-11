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
                return (
                    f"Plan file already exists at: {existing_path}\n"
                    "Proceed with the 5-phase workflow."
                )

        workspace_root = agent.deep_config.workspace.root_path
        slug = get_or_create_plan_slug(workspace_root)
        plan_path = resolve_plan_file_path(workspace_root, slug)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.touch()

        state.plan_mode.plan_slug = slug
        agent.save_state(session, state)

        return (
            f"Plan file created at: {plan_path}\n\n"
            "You should now explore the codebase and design an implementation approach.\n"
            "DO NOT edit any files except the plan file.\n"
            "Use write_file/edit_file to write your plan to the above file path.\n"
            "Follow the 5-phase workflow in your instructions."
        )

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
        if not plan_text.strip():
            return (
                "Plan mode ended. You can now exit the turn.\n"
                f"Plan file: {plan_path_str}"
            )

        return (
            "Plan mode ended. \n"
            f"Plan file: {plan_path_str}\n\n"
            f"## Plan:\n{plan_text}"
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


__all__ = [
    "EnterPlanModeTool",
    "ExitPlanModeTool",
    "generate_word_slug",
    "get_or_create_plan_slug",
    "resolve_plan_file_path",
]
