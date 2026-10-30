# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Mount the consultation skill through the existing harness skill loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.harness.moa.builtin_skills import get_builtin_skill_dir
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.skills.mount import SkillResourceValidator, SkillRootMount

CONSULT_SKILL_NAME = "consult-reference-models"
CONSULT_SKILL_ROOT = get_builtin_skill_dir(CONSULT_SKILL_NAME)
CONSULT_SKILL_FILE = CONSULT_SKILL_ROOT / "SKILL.md"


class ConsultationSkillMount:
    """Track only MoA-owned mounts; discovery and reading remain native."""

    def __init__(self, skill_dir: str | None = None) -> None:
        # Both a packaged skill and an explicit candidate are leaf roots, so
        # unrelated builtin skills and sibling candidates stay undiscovered.
        self._skill_root = Path(skill_dir).resolve() if skill_dir is not None else CONSULT_SKILL_ROOT
        self.rail: SkillUseRail | None = None
        self.owned_rail: SkillUseRail | None = None
        self._mount: SkillRootMount | None = None
        self._agent: Any = None
        self._validator = SkillResourceValidator(CONSULT_SKILL_NAME, self._skill_root)

    def prepare(self, agent: Any) -> None:
        """Select the skill rail after all startup rails have been queued."""
        if agent.deep_config.sys_operation is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="MoA consultation skill requires an existing SysOperation.",
            )
        rails = list({id(rail): rail for rail in agent.find_rails_by_type((SkillUseRail,))}.values())
        if len(rails) > 1:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="MoA consultation skill requires a single SkillUseRail.",
            )
        target = rails[0] if rails else None
        if self.rail is not None and self.rail is not target:
            self.unmount(agent)
        self._agent = agent
        if not rails:
            if agent.ability_manager.get("skill_tool") is not None:
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg="MoA consultation skill conflicts with an existing skill_tool.",
                )
            rail = SkillUseRail(
                skills_dir=[],
                skill_mode=SkillUseRail.SKILL_MODE_ALL,
                include_tools=False,
            )
            self.rail = self.owned_rail = rail
            agent.add_rail(rail)
        else:
            self.rail = rails[0]
        self._check_source()

    def mount(self, agent: Any) -> None:
        """Attach the root in the same init that registers consultation."""
        if self.rail is None or self._agent is not agent:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="MoA consultation skill must be prepared during DeepAgent initialization.",
            )
        self._check_tool()
        self._check_source()
        if self._mount is None:
            self._mount = self.rail.mount_skill_root(self._skill_root)
        else:
            self._mount.ensure_mounted()

    async def validate(self, session: Any = None, *, refresh: bool = False) -> None:
        """Fail before model use if the native loader cannot expose this skill."""
        if self.rail is None or self._agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="MoA consultation skill has not been mounted.",
            )
        self._check_tool()
        operation = self._agent.deep_config.sys_operation
        if operation is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="MoA consultation skill requires an existing SysOperation.",
            )
        try:
            await self.rail.validate_skill_source(
                CONSULT_SKILL_NAME,
                self._skill_root,
                session,
                refresh=refresh,
            )
            await self._validator.validate(operation)
        except ValueError as exc:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg=f"MoA consultation skill {exc}",
                cause=exc,
            ) from exc

    def unmount(self, agent: Any) -> None:
        """Remove only roots introduced by this mount, keeping user settings."""
        if self._mount is not None:
            self._mount.unmount()
        if self.owned_rail is not None:
            agent.system_prompt_builder.remove_section(SectionName.SKILLS)
        self.rail = self.owned_rail = None
        self._mount = None
        self._agent = None
        self._validator.clear()

    def _check_tool(self) -> None:
        card = self._agent.ability_manager.get("skill_tool")
        if not self.rail.owns_tool(card):
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="MoA consultation skill requires its SkillUseRail's native skill_tool.",
            )

    def _check_source(self) -> None:
        try:
            self.rail.check_skill_source(CONSULT_SKILL_NAME, self._skill_root)
        except ValueError as exc:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg=f"MoA consultation skill {exc}",
                cause=exc,
            ) from exc
