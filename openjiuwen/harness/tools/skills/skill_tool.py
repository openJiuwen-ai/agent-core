# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.core.sys_operation.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.skills.markdown_media import (
    markdown_has_image_reference,
    markdown_has_video_reference,
)
from openjiuwen.harness.tools import ToolOutput

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
        enable_read_image_multimodal: bool = True,
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
        self.enable_read_image_multimodal = enable_read_image_multimodal

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
