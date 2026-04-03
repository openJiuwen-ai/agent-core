# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillEvolutionRail for online auto-evolution."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, List, Optional, Union

from openjiuwen.agent_evolving.online.evolver import SkillEvolver
from openjiuwen.agent_evolving.online.schema import (
    EvolutionRecord,
    EvolutionSignal,
    EvolutionContext,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.online.signal_detector import SignalDetector
from openjiuwen.agent_evolving.online.store import EvolutionStore
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail

_MAX_PROCESSED_SIGNAL_KEYS = 500


class SkillEvolutionRail(DeepAgentRail):
    """Online auto-evolution rail for skill patching and persistence."""

    priority = 80
    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        llm: Any,
        model: str,
        auto_scan: bool = True,
        auto_save: bool = True,
        language: str = "cn",
    ) -> None:
        super().__init__()
        self._store = EvolutionStore(skills_dir)
        self._evolver = SkillEvolver(llm, model, language)
        self._auto_scan = auto_scan
        self._processed_signal_keys: set[tuple[str, str]] = set()
        self._auto_save = auto_save
        self._pending_approval_events: list[OutputSchema] = []
    
    @property
    def store(self) -> EvolutionStore:
        return self._store

    @property
    def evolver(self) -> SkillEvolver:
        return self._evolver

    @property
    def processed_signal_keys(self) -> set[tuple[str, str]]:
        return self._processed_signal_keys

    @property
    def auto_save(self) -> bool:
        return self._auto_save

    @auto_save.setter
    def auto_save(self, value: bool) -> None:
        self._auto_save = value

    @property
    def auto_scan(self) -> bool:
        return self._auto_scan

    @auto_scan.setter
    def auto_scan(self, value: bool) -> None:
        self._auto_scan = value
    
    def set_sys_operation(self, sys_operation: SysOperation) -> None:
        super().set_sys_operation(sys_operation)
        self._store.sys_operation = sys_operation

    def update_llm(self, llm: Any, model: str) -> None:
        """Hot-update LLM client and model."""
        self._evolver.update_llm(llm, model)

    def clear_processed_signals(self) -> None:
        """Clear signal fingerprints, typically on conversation boundary."""
        self._processed_signal_keys.clear()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Inject body experiences when reading SKILL.md through read_file tool."""
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return

        tool_name = str(inputs.tool_name or "")
        if "read" not in tool_name.lower() and "file" not in tool_name.lower():
            return

        file_path = self._extract_file_path(inputs.tool_args)
        if not file_path:
            return

        matched = self._SKILL_MD_RE.search(file_path)
        if not matched:
            return

        skill_name = matched.group(1)
        body_text = await self._store.format_body_experience_text(skill_name)
        if not body_text:
            return

        tool_msg = inputs.tool_msg
        if tool_msg is None:
            return

        original = tool_msg.content if isinstance(tool_msg.content, str) else str(tool_msg.content)
        tool_msg.content = original + body_text
        inputs.tool_msg = tool_msg
        logger.info("[SkillEvolutionRail] injected body experience for skill=%s", skill_name)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Run auto-evolution after one invoke round is completed."""
        if not self._auto_scan:
            return

        try:
            parsed_messages = await self._collect_parsed_messages(ctx)
            if not parsed_messages:
                return

            skill_names = self._store.list_skill_names()
            if not skill_names:
                return

            signals = self._detect_signals(parsed_messages, skill_names)
            if not signals:
                return

            skill_groups: dict[str, List[EvolutionSignal]] = {}
            for signal in signals:
                if not signal.skill_name:
                    continue
                skill_groups.setdefault(signal.skill_name, []).append(signal)

            for skill_name, skill_signals in skill_groups.items():
                records = await self._generate_experience_for_skill(
                    skill_name,
                    skill_signals,
                    parsed_messages,
                )
                if records:
                    if self._auto_save:
                        for record in records:
                            await self._store.append_record(skill_name, record)
                        logger.info(
                            "[SkillEvolutionRail] persisted %d record(s) for skill=%s",
                            len(records),
                            skill_name,
                        )
                    else:
                        await self._emit_generated_records(
                            ctx,
                            skill_name=skill_name,
                            records=records,
                        )
        except Exception as exc:
            logger.warning("[SkillEvolutionRail] auto evolution failed: %s", exc)

    def drain_pending_approval_events(self) -> list[OutputSchema]:
        """Return and clear buffered approval events.

        Called by the host (JiuWenClaw) after the streaming loop ends,
        because ``after_invoke`` fires *after* the session stream is
        closed and ``session.write_stream`` can no longer deliver data.
        """
        events = list(self._pending_approval_events)
        self._pending_approval_events.clear()
        return events

    async def _emit_generated_records(
        self,
        ctx: AgentCallbackContext,
        skill_name: str,
        records: List[EvolutionRecord],
    ) -> None:
        """Buffer an approval-request OutputSchema for later delivery.

        The event is stored in ``_pending_approval_events`` because
        ``after_invoke`` runs after the session stream is already
        closed; writing to the stream at this point would be lost.
        The host drains these events via ``drain_pending_approval_events``.
        """
        request_id = f"skill_evolve_approve_{uuid.uuid4().hex[:8]}"
        questions = []
        for record in records:
            content_preview = record.change.content[:1000]
            section = record.change.section
            target_tag = record.change.target.value
            questions.append({
                "question": (
                    f"**Skill '{skill_name}' 演进生成了新经验：**\n\n"
                    f"- **目标**: {target_tag}\n"
                    f"- **章节**: {section}\n\n"
                    f"{content_preview}"
                ),
                "header": "技能演进审批",
                "options": [
                    {"label": "接收", "description": "保留此演进经验"},
                    {"label": "拒绝", "description": "丢弃此演进经验"},
                ],
                "multi_select": False,
            })

        event = OutputSchema(
            type="chat.ask_user_question",
            index=0,
            payload={
                "request_id": request_id,
                "questions": questions,
                "_evolution_data": {
                    "skill_name": skill_name,
                    "records": [record.to_dict() for record in records],
                },
            },
        )
        self._pending_approval_events.append(event)
        logger.info(
            "[SkillEvolutionRail] buffered approval request (%s) with %d record(s) for skill=%s",
            request_id,
            len(records),
            skill_name,
        )

    async def _collect_parsed_messages(self, ctx: AgentCallbackContext) -> List[dict]:
        messages: List[Any] = []

        # 1) preferred path: context directly carried on callback context
        if ctx.context is not None:
            try:
                messages = list(ctx.context.get_messages())
            except Exception as exc:
                logger.debug("[SkillEvolutionRail] read ctx.context messages failed: %s", exc)

        # 2) fallback for DeepAgent outer AFTER_INVOKE hooks: load from inner context engine
        if not messages and ctx.session is not None:
            agent_obj = ctx.agent
            inner_agent = getattr(agent_obj, "_react_agent", None)
            if inner_agent is not None and hasattr(inner_agent, "context_engine"):
                try:
                    context = await inner_agent.context_engine.create_context(session=ctx.session)
                    messages = list(context.get_messages())
                except Exception as exc:
                    logger.debug(
                        "[SkillEvolutionRail] load messages from inner context_engine failed: %s",
                        exc,
                    )

        return self._parse_messages(messages)

    def _detect_signals(
        self,
        parsed_messages: List[dict],
        skill_names: List[str],
    ) -> List[EvolutionSignal]:
        existing_skills = {
            name
            for name in skill_names
            if self._store.skill_exists(name)
        }
        detector = SignalDetector(existing_skills=existing_skills)
        detected = detector.detect(parsed_messages)

        new_signals = [
            signal
            for signal in detected
            if (signal.signal_type, signal.excerpt[:100]) not in self._processed_signal_keys
        ]
        for signal in new_signals:
            self._processed_signal_keys.add((signal.signal_type, signal.excerpt[:100]))

        if len(self._processed_signal_keys) > _MAX_PROCESSED_SIGNAL_KEYS:
            self._processed_signal_keys.clear()

        if new_signals:
            logger.info(
                "[SkillEvolutionRail] detected %d new signal(s), filtered=%d",
                len(new_signals),
                len(detected) - len(new_signals),
            )
        return new_signals

    async def _generate_experience_for_skill(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
    ) -> List[EvolutionRecord]:
        context = EvolutionContext(
            skill_name=skill_name,
            signals=signals,
            skill_content=await self._store.read_skill_content(skill_name),
            messages=messages,
            existing_desc_records=await self._store.get_pending_records(skill_name, EvolutionTarget.DESCRIPTION),
            existing_body_records=await self._store.get_pending_records(skill_name, EvolutionTarget.BODY),
        )
        try:
            return await self._evolver.generate_skill_experience(context)
        except Exception as exc:
            logger.warning(
                "[SkillEvolutionRail] generate failed (skill=%s): %s",
                skill_name,
                exc,
            )
            return []
            
    @classmethod
    def _extract_file_path(cls, tool_args: Any) -> str:
        if isinstance(tool_args, dict):
            args = tool_args
        elif isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
                args = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = {}
        file_path = args.get("file_path", "")
        return str(file_path) if file_path else ""

    @classmethod
    def _parse_messages(cls, messages: List[Any]) -> List[dict]:
        result: List[dict] = []
        for message in messages:
            if isinstance(message, dict):
                result.append(message)
                continue

            role = getattr(message, "role", "")
            content = str(getattr(message, "content", "") or "")

            item: dict = {"role": role, "content": content}

            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                item["tool_calls"] = [
                    {
                        "id": getattr(tool_call, "id", ""),
                        "name": getattr(tool_call, "name", ""),
                        "arguments": getattr(tool_call, "arguments", ""),
                    }
                    for tool_call in tool_calls
                ]

            name = getattr(message, "name", None)
            if name:
                item["name"] = name

            result.append(item)
        return result


__all__ = ["SkillEvolutionRail"]
