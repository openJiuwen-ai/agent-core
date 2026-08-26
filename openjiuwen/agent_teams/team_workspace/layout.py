# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workspace internal path rules.

Single source of truth for the relative paths *inside* a team workspace
directory (everything under ``<team>/team-workspace/``) plus the framework
source roots the assembler/loader read from. ``agent_teams.paths`` locates
the team/member roots on disk; this module locates the evolvable text
sub-trees inside the workspace root.

The layout is data-driven: every mapping (framework source → workspace
target) is one constant + one method pair. Adding a new evolvable text class
(e.g. ``mode/<lang>/*.md → prompts/mode/system/<name>.<lang>.md``) is adding
one row here, not editing three classes.

``WorkspaceLayout`` is stateless — pure ``Path`` in / ``Path`` out, so the
writer (``WorkspaceAssembler`` / ``WorkspaceStore``) and the reader
(``WorkspaceCache`` / ``prompts.loader``) share the exact same rules with no
instance injection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

# Workspace sub-directories for each evolvable text class. These are the
# *only* places A/B/C class files land inside ``team-workspace/``.
PROMPTS_SYSTEM = "prompts/system"  # A class (lang-suffixed)
PROMPTS_TOOL = "prompts/tool"  # C class (md + param)
PROMPTS_IDENTITY = "prompts/identity"  # B class team (no lang)

# Fixed file names inside the workspace.
TEAM_CARD_FILE = "team_card.md"
TEAM_PROMPT_FILE = "team_prompt.md"
MEMBER_CARD_FILE = "card.md"
MEMBER_PROMPT_FILE = "member_prompt.md"
TOOL_PARAM_FILE_FMT = "tool.param.{lang}.md"  # C param-level (flat at tool root)

# Member-internal sub-directory for B-class member files (relative to the
# member's real directory, NOT the workspace root).
MEMBER_IDENTITY_REL = "prompts/identity"

# Framework source roots (relative to the ``agent_teams/`` package root).
_FRAMEWORK_PROMPTS_REL = "prompts"
_FRAMEWORK_LOCALES_REL = "tools/locales"
_DESC_SUBDIR = "descs"

# The ``agent_teams/`` package root — single anchor for framework sources.
# ``layout.py`` lives in ``team_workspace/``, one level below.
_AGENT_TEAMS_PKG_ROOT = Path(__file__).resolve().parents[1]


class WorkspaceLayout:
    """Pure path rules for the inside of a team workspace.

    All layout knowledge lives here so ``WorkspaceAssembler`` (writer),
    ``WorkspaceCache`` (reader) and ``WorkspaceStore`` never hand-assemble
    ``prompts/system`` / ``prompts/tool`` / ``prompts/identity`` segments. One
    change here propagates to both sides. Methods are static and stateless —
    they take ``Path`` arguments and return ``Path`` / yield iterators.
    """

    # ── A class (prompt templates) ─────────────────────────────────

    @staticmethod
    def system_dir(workspace_root: Path) -> Path:
        """``<workspace_root>/prompts/system`` — A-class target dir."""
        return workspace_root / PROMPTS_SYSTEM

    @staticmethod
    def system_file(workspace_root: Path, name: str, lang: str) -> Path:
        """``<workspace_root>/prompts/system/<name>.<lang>.md``."""
        return WorkspaceLayout.system_dir(workspace_root) / f"{name}.{lang}.md"

    @staticmethod
    def iter_system_files(workspace_root: Path, lang: str) -> Iterator[Path]:
        """Yield every ``<name>.<lang>.md`` under system_dir (sorted)."""
        subdir = WorkspaceLayout.system_dir(workspace_root)
        if not subdir.is_dir():
            return
        yield from sorted(subdir.glob(f"*.{lang}.md"))

    # ── C class (tool descriptions) ────────────────────────────────

    @staticmethod
    def tool_dir(workspace_root: Path) -> Path:
        """``<workspace_root>/prompts/tool`` — C-class target dir."""
        return workspace_root / PROMPTS_TOOL

    @staticmethod
    def tool_md_file(tool_dir: Path, rel_dir: Path, key: str, lang: str) -> Path:
        """``<tool_dir>/<rel_dir>/<key>.<lang>.md`` (rel_dir may be '.')."""
        return tool_dir / rel_dir / f"{key}.{lang}.md"

    @staticmethod
    def tool_param_file(tool_dir: Path, lang: str) -> Path:
        """``<tool_dir>/tool.param.<lang>.md`` — flat at the tool root."""
        return tool_dir / TOOL_PARAM_FILE_FMT.format(lang=lang)

    @staticmethod
    def iter_tool_md_files(tool_dir: Path, lang: str) -> Iterator[Path]:
        """Yield every ``*.<lang>.md`` under tool_dir (rglob, sorted), excluding the param file."""
        param_name = TOOL_PARAM_FILE_FMT.format(lang=lang)
        if not tool_dir.is_dir():
            return
        for path in sorted(tool_dir.rglob(f"*.{lang}.md")):
            if path.name == param_name:
                continue
            yield path

    @staticmethod
    def iter_tool_param_file(tool_dir: Path, lang: str) -> Iterator[Path]:
        """Yield the single ``tool.param.<lang>.md`` if it exists."""
        param = tool_dir / TOOL_PARAM_FILE_FMT.format(lang=lang)
        if param.is_file():
            yield param

    # ── B class (team identity files) ──────────────────────────────

    @staticmethod
    def team_identity_dir(workspace_root: Path) -> Path:
        """``<workspace_root>/prompts/identity`` — B-class team dir."""
        return workspace_root / PROMPTS_IDENTITY

    @staticmethod
    def team_card_file(workspace_root: Path) -> Path:
        """``<workspace_root>/prompts/identity/team_card.md``."""
        return WorkspaceLayout.team_identity_dir(workspace_root) / TEAM_CARD_FILE

    @staticmethod
    def team_prompt_file(workspace_root: Path) -> Path:
        """``<workspace_root>/prompts/identity/team_prompt.md``."""
        return WorkspaceLayout.team_identity_dir(workspace_root) / TEAM_PROMPT_FILE

    # ── B class (member identity files) ────────────────────────────

    @staticmethod
    def member_card_file(member_dir: Path) -> Path:
        """``<member_dir>/prompts/identity/card.md``."""
        return member_dir / MEMBER_IDENTITY_REL / MEMBER_CARD_FILE

    @staticmethod
    def member_prompt_file(member_dir: Path) -> Path:
        """``<member_dir>/prompts/identity/member_prompt.md``."""
        return member_dir / MEMBER_IDENTITY_REL / MEMBER_PROMPT_FILE

    # ── framework source roots (assembler/loader read side) ────────

    @staticmethod
    def framework_prompts_dir(lang: str) -> Path:
        """``<agent_teams>/prompts/<lang>`` — A-class source dir."""
        return _AGENT_TEAMS_PKG_ROOT / _FRAMEWORK_PROMPTS_REL / lang

    @staticmethod
    def framework_prompt_file(name: str, lang: str) -> Path:
        """``<agent_teams>/prompts/<lang>/<name>.md`` — A-class source file."""
        return _AGENT_TEAMS_PKG_ROOT / _FRAMEWORK_PROMPTS_REL / lang / f"{name}.md"

    @staticmethod
    def iter_framework_prompt_files(lang: str) -> Iterator[Path]:
        """Yield every ``*.md`` under ``prompts/<lang>/`` (sorted)."""
        prompts_dir = _AGENT_TEAMS_PKG_ROOT / _FRAMEWORK_PROMPTS_REL / lang
        if not prompts_dir.is_dir():
            return
        yield from sorted(prompts_dir.glob("*.md"))

    @staticmethod
    def framework_descs_dir(lang: str) -> Path:
        """``<agent_teams>/tools/locales/descs/<lang>`` — C-class source dir."""
        return _AGENT_TEAMS_PKG_ROOT / _FRAMEWORK_LOCALES_REL / _DESC_SUBDIR / lang

    @staticmethod
    def iter_framework_desc_files(lang: str) -> Iterator[Path]:
        """Yield every ``*.md`` under ``descs/<lang>/`` (rglob, sorted)."""
        descs_dir = _AGENT_TEAMS_PKG_ROOT / _FRAMEWORK_LOCALES_REL / _DESC_SUBDIR / lang
        if not descs_dir.is_dir():
            return
        yield from sorted(descs_dir.rglob("*.md"))


__all__ = [
    "WorkspaceLayout",
    "PROMPTS_SYSTEM",
    "PROMPTS_TOOL",
    "PROMPTS_IDENTITY",
    "MEMBER_IDENTITY_REL",
    "TEAM_CARD_FILE",
    "TEAM_PROMPT_FILE",
    "MEMBER_CARD_FILE",
    "MEMBER_PROMPT_FILE",
    "TOOL_PARAM_FILE_FMT",
]
