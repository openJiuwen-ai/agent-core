# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RSI-owned adapters for the public Agent Core and Harness APIs."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.skills import build_skill_line, build_skill_lines
from openjiuwen.harness.rails._multimodal import should_enable_read_image_multimodal
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.tools import BashTool, PowerShellTool
from openjiuwen.harness.tools.code import CodeTool
from openjiuwen.harness.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from openjiuwen.harness.tools.skills.list_skill import ListSkillTool
from openjiuwen.harness.tools.skills.skill_tool import SkillTool

_ACTIVE_SKILL_SECTION = "rsi.active_skill"


class RSIBashTool(BashTool):
    """Bash tool variant that preserves producer failures in pipelines."""

    def __init__(self, *args: Any, pipefail: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pipefail = bool(pipefail)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any):
        if not self._pipefail:
            return await super().invoke(inputs, **kwargs)

        adapted = dict(inputs)
        command = str(adapted.get("command", "") or "").strip()
        if command and not command.startswith("set -o pipefail;"):
            adapted["command"] = f"set -o pipefail; {command}"
        adapted["shell_type"] = "bash"
        return await super().invoke(adapted, **kwargs)


class RSISysOperationRail(SysOperationRail):
    """Mount upstream system-operation tools with RSI evaluation policy."""

    def __init__(
        self,
        *,
        shell_only: bool = False,
        bash_pipefail: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._shell_only = bool(shell_only)
        self._bash_pipefail = bool(bash_pipefail)

    def init(self, agent: Any) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        bash_tool = RSIBashTool(
            self.sys_operation,
            lang,
            agent_id=agent_id,
            deny_patterns=self._bash_deny_patterns,
            pipefail=self._bash_pipefail,
        )

        if self._shell_only:
            self.tools = [bash_tool]
        else:
            enable_image = should_enable_read_image_multimodal(
                agent,
                self._enable_read_image_multimodal,
            )
            read_tool = ReadFileTool(
                self.sys_operation,
                lang,
                agent_id,
                enable_image_multimodal=enable_image,
            )
            shared = [
                GlobTool(self.sys_operation, lang, agent_id),
                ListDirTool(self.sys_operation, lang, agent_id),
                GrepTool(self.sys_operation, lang, agent_id),
                bash_tool,
            ]
            if self._read_only:
                self.tools = [read_tool, *shared]
            else:
                self.tools = [
                    read_tool,
                    WriteFileTool(self.sys_operation, lang, agent_id),
                    EditFileTool(self.sys_operation, lang, agent_id),
                    *shared,
                ]
            if os.name == "nt":
                self.tools.append(PowerShellTool(self.sys_operation, lang, agent_id=agent_id))
            if self._with_code_tool and not self._read_only:
                self.tools.append(CodeTool(self.sys_operation, lang, agent_id))

        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)


class RSISkillUseRail(SkillUseRail):
    """Adapt upstream SkillUseRail to RSI's measured delivery protocol."""

    def __init__(
        self,
        *args: Any,
        trigger_at_task_start: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.trigger_at_task_start = bool(trigger_at_task_start)
        self._runtime_skill_tool: SkillTool | None = None
        self._task_trigger_evidence: dict[str, Any] = {}

    def init(self, agent: Any) -> None:
        super().init(agent)
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        self._runtime_skill_tool = SkillTool(
            operation=self.sys_operation,
            get_skills=lambda session=None: self.get_skills_for_session(session),
            language=lang,
            agent_id=agent_id,
            multimodal_skill_mode=self.multimodal_skill_mode,
            enable_read_image_multimodal=should_enable_read_image_multimodal(agent),
        )

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await super().before_invoke(ctx)
        await self._trigger_relevant_skill(ctx)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        await self._clear_active_skill_attachment(ctx)
        await super().after_invoke(ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        if getattr(inputs, "tool_name", "") != "skill_tool":
            return

        tool_args = self._normalize_tool_args(getattr(inputs, "tool_args", None))
        relative_path = str(tool_args.get("relative_file_path") or "SKILL.md").strip()
        if relative_path != "SKILL.md":
            return

        result = getattr(inputs, "tool_result", None)
        success = getattr(result, "success", None)
        data = getattr(result, "data", None)
        if isinstance(result, dict):
            success = result.get("success", success)
            data = result.get("data", data)
        if success is not True or not isinstance(data, dict):
            return

        skill_content = str(data.get("skill_content") or "").strip()
        if not skill_content or self.attachment_manager is None:
            return

        skill_name = str(tool_args.get("skill_name") or "").strip() or "selected skill"
        decision_capsule = self._extract_decision_capsule(skill_content)
        active_content = decision_capsule or skill_content
        memo = self._build_active_skill_attachment(
            skill_name,
            active_content,
            decision_capsule=bool(decision_capsule),
        )
        writer = self.attachment_manager.bind_context(ctx)
        if not writer.session_id:
            return
        await writer.add_section(
            section=_ACTIVE_SKILL_SECTION,
            content=memo,
            kind=PromptAttachmentKind.SKILL,
            source="rsi.skill_delivery",
            priority=35,
            content_kind="text/markdown",
            metadata={
                "skill_name": skill_name,
                "skill_content_chars": len(skill_content),
                "skill_content_sha256": hashlib.sha256(skill_content.encode("utf-8")).hexdigest(),
                "delivery_mode": ("decision_capsule" if decision_capsule else "full_fallback"),
            },
        )

    def _build_skills_section(self, skills=None):
        skills = self.skills if skills is None else skills
        if self.skill_mode == self.SKILL_MODE_AUTO_LIST:
            content = (
                "# Skills\n\nCall list_skill when relevant, then load the selected "
                "skill with skill_tool before investigating or editing."
            )
        elif skills:
            lines = build_skill_lines(
                build_skill_line(
                    index=index,
                    skill_name=skill.name,
                    description=skill.description,
                )
                for index, skill in enumerate(skills, start=1)
            )
            content = (
                "# Skills\n\nWhen a listed skill is relevant, call skill_tool with its "
                "exact name before investigating or editing. Keep the loaded skill's "
                "decision capsule active through verification.\n\nAvailable skills:\n"
                f"{lines}"
            )
        else:
            content = "# Skills\n\nNo skills are available for this task."
        return PromptSection(
            name=SectionName.SKILLS,
            content={"cn": content, "en": content},
            priority=90,
        )

    async def _trigger_relevant_skill(self, ctx: AgentCallbackContext) -> None:
        self._task_trigger_evidence = {
            "mode": "task_start_metadata_trigger",
            "attempted": False,
            "selected_skill_name": "",
            "delivered": False,
            "reason": "disabled",
        }
        if not self.trigger_at_task_start:
            return
        if self.list_skill_model is None:
            self._task_trigger_evidence["reason"] = "routing_model_unavailable"
            return
        if not self.skills:
            self._task_trigger_evidence["reason"] = "no_available_skills"
            return
        if self._runtime_skill_tool is None:
            self._task_trigger_evidence["reason"] = "skill_tool_unavailable"
            return

        query = self._task_query(getattr(ctx.inputs, "query", ""))
        if not query:
            self._task_trigger_evidence["reason"] = "empty_task"
            return

        self._task_trigger_evidence["attempted"] = True
        selector = ListSkillTool(
            get_skills=lambda session=None: self.get_skills_for_session(session),
            list_skill_model=self.list_skill_model,
            language=getattr(self.system_prompt_builder, "language", "en"),
        )
        result = await selector.invoke(
            {"query": query},
            session=getattr(ctx, "session", None),
        )
        if not result.success:
            self._task_trigger_evidence.update(
                reason="routing_failed",
                error=str(result.error or "skill routing failed"),
            )
            return

        data = result.data if isinstance(result.data, dict) else {}
        selected_names = [str(name).strip() for name in data.get("selected_skill_names", []) if str(name).strip()]
        if not selected_names:
            self._task_trigger_evidence["reason"] = "no_relevant_skill"
            return

        skill_name = selected_names[0]
        load_args = {"skill_name": skill_name, "relative_file_path": "SKILL.md"}
        load_result = await self._runtime_skill_tool.invoke(
            load_args,
            session=getattr(ctx, "session", None),
        )
        self._task_trigger_evidence["selected_skill_name"] = skill_name
        if not load_result.success:
            self._task_trigger_evidence.update(
                reason="skill_load_failed",
                error=str(load_result.error or "skill load failed"),
            )
            return

        await self.after_tool_call(
            AgentCallbackContext(
                agent=ctx.agent,
                inputs=ToolCallInputs(
                    tool_name="skill_tool",
                    tool_args=load_args,
                    tool_result=load_result,
                ),
                session=ctx.session,
                context=ctx.context,
                extra=ctx.extra,
            )
        )
        self._task_trigger_evidence.update(delivered=True, reason="loaded")
        logger.info("[RSISkillUseRail] task-start Skill loaded: %s", skill_name)

    def task_trigger_evidence(self) -> dict[str, Any]:
        return dict(self._task_trigger_evidence)

    @staticmethod
    def _task_query(raw: object) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, dict):
            return json.dumps(raw, ensure_ascii=False, sort_keys=True)
        return str(raw).strip()

    @staticmethod
    def _normalize_tool_args(raw: object) -> dict[str, object]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _extract_decision_capsule(content: str) -> str:
        lines = content.splitlines()
        start = next(
            (index for index, line in enumerate(lines) if line.strip().lower() == "## decision capsule"),
            -1,
        )
        if start < 0:
            return ""
        content_start = start + 1
        end = next(
            (index for index, line in enumerate(lines[content_start:], start=content_start) if line.startswith("## ")),
            len(lines),
        )
        return "\n".join(lines[start:end]).strip()

    @staticmethod
    def _build_active_skill_attachment(
        skill_name: str,
        content: str,
        *,
        decision_capsule: bool = False,
    ) -> str:
        source_label = "decision capsule" if decision_capsule else "Skill instructions"
        return (
            f"# Active Skill: {skill_name}\n\n"
            "This skill was already loaded successfully. Keep its requirements active "
            "through diagnosis, editing, and verification. Do not reload it; act on "
            f"the {source_label} below.\n\n{content.strip()}"
        )

    async def _clear_active_skill_attachment(self, ctx: AgentCallbackContext) -> None:
        if self.attachment_manager is None:
            return
        writer = self.attachment_manager.bind_context(ctx)
        if writer.session_id:
            await writer.clear_section(_ACTIVE_SKILL_SECTION)


async def run_agent_with_empty_response_recovery(
    agent: Any,
    inputs: dict[str, Any],
    *,
    session: str,
) -> Any:
    """Retry one RSI evaluation invoke when it returns no actionable output."""

    response = await Runner.run_agent(agent, inputs, session=session)
    if _response_text(response).strip():
        return response

    logger.warning("[RSI evaluator] recovering once from an empty agent response")
    return await Runner.run_agent(
        agent,
        {
            "query": (
                "[RECOVERY] The previous turn produced no answer or tool action. "
                "Do not restart the analysis. Apply the smallest justified workspace "
                "edit now, then run the acceptance probe."
            )
        },
        session=session,
    )


def _response_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        recognized = False
        for key in ("output", "content", "response", "result"):
            if key in value:
                recognized = True
                text = _response_text(value[key])
                if text:
                    return text
        return "" if recognized else json.dumps(value, ensure_ascii=False)
    return str(value)


__all__ = [
    "RSIBashTool",
    "RSISkillUseRail",
    "RSISysOperationRail",
    "run_agent_with_empty_response_recovery",
]
