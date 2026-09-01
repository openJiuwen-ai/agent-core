# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.core.sys_operation.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.skills.markdown_media import (
    markdown_has_image_reference,
    markdown_has_video_reference,
)
from openjiuwen.harness.tools import ToolOutput

# Fixed limits for default directory layout (not exposed as tool parameters).
_TREE_MAX_DEPTH = 4
_TREE_MAX_LINES = 200
_MAX_NESTED_SKILL_NAMES = 50
_TREE_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    "output",
    "temp",
    "assets",
    "node_modules",
})

SKILL_TOOL_MARKDOWN_IMAGES_HINT = (
    "Embedded figures in this skill are markdown links (paths/URLs) only; pixel data is not "
    "attached. Call read_file on the image path under skills/<skill-name>/… when you need "
    "to inspect a reference screenshot."
)

SKILL_TOOL_MARKDOWN_IMAGES_VISION_HINT = (
    "Embedded figures in this skill are markdown links (paths/URLs) only; pixel data is not "
    "attached. read_file native image multimodal input is disabled. If a vision tool is "
    "configured, call visual_question_answering on the image path under "
    "skills/<skill-name>/… when you need to inspect a reference screenshot."
)

SKILL_TOOL_MARKDOWN_VIDEOS_HINT = (
    "Embedded videos in this skill are link references only. Skill videos are consumed in "
    "branch mode (multimodal_skill_mode=branch); do not read_file skill videos on the "
    "main agent loop as it makes the context window too large."
)


def _strip_skill_tool_injected_hints(body: str) -> str:
    s = body
    while True:
        stripped = False
        for prefix in (
            SKILL_TOOL_MARKDOWN_IMAGES_HINT + "\n\n",
            SKILL_TOOL_MARKDOWN_IMAGES_VISION_HINT + "\n\n",
            SKILL_TOOL_MARKDOWN_VIDEOS_HINT + "\n\n",
        ):
            if s.startswith(prefix):
                s = s[len(prefix):]
                stripped = True
        if not stripped:
            break
    return s


def apply_skill_tool_markdown_images_hint(
    body: str,
    *,
    enable_read_image_multimodal: bool = True,
) -> str:
    """Normalize body and prepend skill-tool media hints at most once."""
    normalized = _strip_skill_tool_injected_hints(body)
    hints: List[str] = []
    if markdown_has_image_reference(normalized):
        if enable_read_image_multimodal:
            hints.append(SKILL_TOOL_MARKDOWN_IMAGES_HINT)
        else:
            hints.append(SKILL_TOOL_MARKDOWN_IMAGES_VISION_HINT)
    if markdown_has_video_reference(normalized):
        hints.append(SKILL_TOOL_MARKDOWN_VIDEOS_HINT)
    if not hints:
        return normalized
    return "\n\n".join(hints) + "\n\n" + normalized


def skill_markdown_has_media(skill_content: str) -> bool:
    return markdown_has_image_reference(skill_content) or markdown_has_video_reference(
        skill_content
    )


def _is_safe_relative_file_path(file_path: str) -> bool:
    """Return whether a skill file path is relative and contains no traversal."""
    posix_path = PurePosixPath(file_path.replace("\\", "/"))
    windows_path = PureWindowsPath(file_path)
    return (
        not posix_path.is_absolute()
        and not windows_path.drive
        and not windows_path.root
        and ".." not in posix_path.parts
    )


def _skill_tree_skip_name(name: str, *, is_dir: bool) -> bool:
    if name.startswith("."):
        return True
    if is_dir and name in _TREE_SKIP_DIR_NAMES:
        return True
    return False


def _append_skill_ascii_tree_lines(
    directory: Path,
    prefix: str,
    *,
    max_depth: int,
    dir_depth: int,
    out: List[str],
    max_lines: int,
    visited: set[str],
) -> bool:
    """Append UTF-8 tree lines under ``directory``; return True if line budget exhausted."""
    if dir_depth > max_depth or len(out) >= max_lines:
        return len(out) >= max_lines
    try:
        dir_key = str(directory.resolve())
    except OSError:
        dir_key = str(directory)
    if dir_key in visited:
        return False
    visited.add(dir_key)
    try:
        raw = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return False
    children = [
        p
        for p in raw
        if not _skill_tree_skip_name(p.name, is_dir=p.is_dir())
    ]
    for i, path in enumerate(children):
        if len(out) >= max_lines:
            return True
        is_last = i == len(children) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = "    " if is_last else "│   "
        display = f"{path.name}/" if path.is_dir() else path.name
        out.append(f"{prefix}{connector}{display}")
        if path.is_dir() and _append_skill_ascii_tree_lines(
            path,
            prefix + child_prefix,
            max_depth=max_depth,
            dir_depth=dir_depth + 1,
            out=out,
            max_lines=max_lines,
            visited=visited,
        ):
            return True
    return False


def _build_skill_directory_ascii_tree(
    skill_root: Path,
    *,
    max_depth: int = _TREE_MAX_DEPTH,
    max_lines: int = _TREE_MAX_LINES,
) -> Tuple[List[str], bool, Optional[str]]:
    """Build an ASCII directory tree for a skill root.

    Returns ``(tree_blocks, truncated, error)``. ``tree_blocks`` is either empty
    or a single-element list containing the full ASCII tree text.
    """
    try:
        resolved = skill_root.resolve()
    except OSError:
        resolved = skill_root
    if not resolved.exists():
        return [], False, "skill directory does not exist"
    if not resolved.is_dir():
        return [], False, "skill path is not a directory"
    root_name = resolved.name or "."
    out: List[str] = [f"{root_name}/"]
    cap = max(2, max_lines)
    truncated = _append_skill_ascii_tree_lines(
        resolved,
        "",
        max_depth=max_depth,
        dir_depth=0,
        out=out,
        max_lines=cap,
        visited=set(),
    )
    return ["\n".join(out)], truncated, None


def _collect_nested_skill_names(
    skill_root: Path,
    *,
    max_names: int = _MAX_NESTED_SKILL_NAMES,
) -> Tuple[List[str], bool]:
    """Collect relative paths of nested dirs that contain ``SKILL.md`` under ``skill_root``."""
    try:
        resolved = skill_root.resolve()
    except OSError:
        resolved = skill_root
    if not resolved.is_dir():
        return [], False

    names: List[str] = []
    truncated = False
    visited: set[str] = set()

    def _walk(directory: Path) -> None:
        nonlocal truncated
        if truncated:
            return
        if len(names) >= max_names:
            truncated = True
            return
        try:
            dir_key = str(directory.resolve())
        except OSError:
            dir_key = str(directory)
        if dir_key in visited:
            return
        visited.add(dir_key)
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if truncated:
                return
            if len(names) >= max_names:
                truncated = True
                return
            if not child.is_dir() or _skill_tree_skip_name(child.name, is_dir=True):
                continue
            try:
                child_resolved = child.resolve()
            except OSError:
                child_resolved = child
            try:
                rel = str(child_resolved.relative_to(resolved)).replace("\\", "/")
            except ValueError:
                # Symlink/mount pointing outside the skill root.
                continue
            if rel in ("", "."):
                # Symlink back to the skill root itself — not a nested skill.
                continue
            child_key = str(child_resolved)
            if child_key in visited:
                continue
            if (child_resolved / "SKILL.md").is_file():
                names.append(rel)
                if len(names) >= max_names:
                    truncated = True
                    return
            _walk(child_resolved)

    _walk(resolved)
    return names, truncated


def _skill_layout_metadata(skill_directory: Path) -> Dict[str, Any]:
    """Build default directory layout fields for a successful skill_tool result."""
    meta: Dict[str, Any] = {}
    tree, tree_truncated, tree_err = _build_skill_directory_ascii_tree(skill_directory)
    if tree:
        meta["directory_tree"] = tree
        meta["directory_tree_truncated"] = tree_truncated
    if tree_err:
        meta["directory_tree_partial_errors"] = tree_err

    nested_names, nested_truncated = _collect_nested_skill_names(skill_directory)
    meta["discovered_skill_names"] = nested_names
    meta["discovered_skill_names_truncated"] = nested_truncated
    return meta


def _format_layout_appendix_for_model(layout: Dict[str, Any]) -> str:
    """Render directory layout for model-facing tool text.

    AbilityManager prefers ``data['content']`` when present and drops other fields.
    Append this block to ``content`` so directory_tree / nested skills stay visible.
    """
    parts: List[str] = []
    tree = layout.get("directory_tree")
    if isinstance(tree, list) and tree:
        tree_text = str(tree[0]).strip()
    elif isinstance(tree, str) and tree.strip():
        tree_text = tree.strip()
    else:
        tree_text = ""
    if tree_text:
        truncated = bool(layout.get("directory_tree_truncated"))
        suffix = "\n…(truncated)" if truncated else ""
        parts.append(f"## Directory layout\n```\n{tree_text}{suffix}\n```")

    names = layout.get("discovered_skill_names")
    if isinstance(names, list) and names:
        lines = "\n".join(f"- {name}/SKILL.md" for name in names if str(name).strip())
        if lines:
            note = ""
            if layout.get("discovered_skill_names_truncated"):
                note = "\n…(truncated)"
            parts.append(
                "## Nested skills\n"
                "Load a nested skill with skill_tool and relative_file_path, e.g. "
                "`designer/SKILL.md`.\n"
                f"{lines}{note}"
            )
    return "\n\n".join(parts)


class SkillTool(Tool):
    """View the skill contents of a certain skill"""
    operation: SysOperation
    get_skills: Callable[..., List[Skill]]

    def __init__(
        self,
        operation: SysOperation,
        get_skills: Callable[..., List[Skill]],
        language: str = "cn",
        agent_id: Optional[str] = None,
        multimodal_skill_mode: str = "hint",
        enable_read_image_multimodal: bool | Callable[[], bool] = True,
    ):
        """Initialize SkillTool.

        Args:
            operation: SysOperation for file system operations to read files
            get_skills: Callable that returns current enabled skills.
            multimodal_skill_mode: ``hint`` (default), ``attach``, or ``branch``.
            enable_read_image_multimodal: When True, image hints recommend read_file;
                when False, image hints recommend vision tools instead.
        """
        super().__init__(
            build_tool_card("skill_tool", "SkillTool", language, agent_id=agent_id)
        )
        self.operation = operation
        self.get_skills = get_skills
        self.language = language
        self.multimodal_skill_mode = multimodal_skill_mode
        self._enable_read_image_multimodal = enable_read_image_multimodal

    @property
    def enable_read_image_multimodal(self) -> bool:
        """Return the current native-image policy used by skill hints."""
        if callable(self._enable_read_image_multimodal):
            return bool(self._enable_read_image_multimodal())
        return self._enable_read_image_multimodal

    @enable_read_image_multimodal.setter
    def enable_read_image_multimodal(
        self,
        value: bool | Callable[[], bool],
    ) -> None:
        self._enable_read_image_multimodal = value

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        """Invoke skill_tool tool."""
        skill_name = str(inputs.get("skill_name", "") or "").strip()
        relative_file_path = str(inputs.get("relative_file_path") or "SKILL.md").strip()

        if not _is_safe_relative_file_path(relative_file_path):
            return ToolOutput(
                success=False,
                error=(
                    "Invalid relative_file_path: absolute paths and '..' traversal "
                    "components are not allowed"
                ),
            )

        try:
            skill = self._get_skill_by_name(skill_name, kwargs.get("session"))
            if not skill:
                return ToolOutput(
                    success=False,
                    error=f"Skill not found: {skill_name}"
                )
            
            file_path = str(Path(skill.directory) / relative_file_path)
            read_file_result = await self.operation.fs().read_file(file_path)
            if read_file_result.code != 0:
                return ToolOutput(
                    success=False,
                    error=read_file_result.message
                )

            skill_file_content = read_file_result.data.content

            data: Dict[str, Any] = {
                "skill_directory": str(skill.directory),
                "skill_content": skill_file_content,
            }
            if (
                self.multimodal_skill_mode == "hint"
                and skill_markdown_has_media(skill_file_content)
            ):
                data["content"] = apply_skill_tool_markdown_images_hint(
                    skill_file_content,
                    enable_read_image_multimodal=self.enable_read_image_multimodal,
                )

            # Local pathlib walks; offload so large skill trees do not block the event loop.
            layout = await asyncio.to_thread(
                _skill_layout_metadata,
                Path(str(skill.directory)),
            )
            data.update(layout)

            # AbilityManager._build_tool_message_content short-circuits on data['content']
            # and would otherwise hide directory_tree / discovered_skill_names from the model.
            appendix = _format_layout_appendix_for_model(layout)
            if appendix and data.get("content"):
                data["content"] = str(data["content"]).rstrip() + "\n\n" + appendix

            return ToolOutput(
                success=True,
                data=data,
            )
        
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
            )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None

    def _get_skill_by_name(self, skill_name: str, session: Any = None) -> Optional[Skill]:
        """Select skill object by name."""
        if not skill_name:
            return None

        try:
            skills = self.get_skills(session=session) or []
        except TypeError:
            # Keep compatibility with callers that provide the original no-arg callback.
            skills = self.get_skills() or []
        skill_map = {skill.name: skill for skill in skills}
        return skill_map.get(skill_name)


__all__ = [
    "SkillTool",
    "SKILL_TOOL_MARKDOWN_IMAGES_HINT",
    "SKILL_TOOL_MARKDOWN_IMAGES_VISION_HINT",
    "SKILL_TOOL_MARKDOWN_VIDEOS_HINT",
    "apply_skill_tool_markdown_images_hint",
    "skill_markdown_has_media",
]
