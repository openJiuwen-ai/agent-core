# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Helpers for evolution approval and progress OutputSchema events."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.harness.rails.evolution.contracts import EvolutionHostEventMeta


def _is_en(language: str) -> bool:
    return str(language).lower() == "en"


def build_progress_event(prefix: str, message: str) -> OutputSchema:
    """Build an llm_reasoning progress event."""
    return OutputSchema(
        type="llm_reasoning",
        index=0,
        payload={"content": f"{prefix} {message}\n"},
    )


def build_evolution_progress_event(
    *,
    rail_kind: str,
    stage: str,
    message: str,
    skill_name: Optional[str] = None,
    request_id: Optional[str] = None,
    prefix: Optional[str] = None,
) -> OutputSchema:
    """Build a normalized evolution progress event."""
    display_prefix = prefix or "[Evolution]"
    payload: Dict[str, Any] = {
        "content": f"{display_prefix} {message}\n",
        "_evolution_meta": EvolutionHostEventMeta(
            event_kind="progress",
            rail_kind=rail_kind,
            stage=stage,
        ).to_payload(),
    }
    if skill_name is not None:
        payload["_evolution_meta"]["skill_name"] = skill_name
    if request_id is not None:
        payload["_evolution_meta"]["request_id"] = request_id
    return OutputSchema(type="llm_reasoning", index=0, payload=payload)


def attach_evolution_meta(
    event: OutputSchema,
    *,
    signal_type: Optional[str] = None,
    signal_source: Optional[str] = None,
) -> OutputSchema:
    """Attach normalized evolution metadata to an approval event payload."""
    evolution_meta = event.payload.setdefault("_evolution_meta", {})
    evolution_meta.setdefault("event_kind", "approval")
    if signal_type is not None:
        evolution_meta["signal_type"] = signal_type
    if signal_source is not None:
        evolution_meta["source"] = signal_source
    return event


def build_skill_approval_event(
    skill_name: str,
    request_id: str,
    records: Iterable[EvolutionRecord],
    language: str = "cn",
) -> OutputSchema:
    """Build a skill experience approval event."""
    questions: List[Dict[str, Any]] = []
    is_en = _is_en(language)
    for record in records:
        questions.append(
            {
                "question": (
                    (
                        f"**Skill '{skill_name}' generated a new experience:**\n\n"
                        f"- **Target**: {record.change.target.value}\n"
                        f"- **Section**: {record.change.section}\n\n"
                        f"{record.change.content[:1000]}"
                    )
                    if is_en else
                    (
                        f"**Skill '{skill_name}' 演进生成了新经验：**\n\n"
                        f"- **目标**: {record.change.target.value}\n"
                        f"- **章节**: {record.change.section}\n\n"
                        f"{record.change.content[:1000]}"
                    )
                ),
                "header": "Skill Evolution Approval" if is_en else "技能演进审批",
                "options": (
                    [
                        {"label": "Accept", "description": "Keep this evolution experience"},
                        {"label": "Reject", "description": "Discard this evolution experience"},
                    ]
                    if is_en else
                    [
                        {"label": "接收", "description": "保留此演进经验"},
                        {"label": "拒绝", "description": "丢弃此演进经验"},
                    ]
                ),
                "multi_select": False,
            }
        )

    return OutputSchema(
        type="chat.ask_user_question",
        index=0,
        payload={
            "request_id": request_id,
            "_evolution_meta": EvolutionHostEventMeta(
                event_kind="approval",
                skill_name=skill_name,
                request_id=request_id,
            ).to_payload(),
            "questions": questions,
        },
    )


def build_simplify_approval_event(
    skill_name: str,
    request_id: str,
    actions: List[Dict[str, Any]],
    language: str = "cn",
) -> OutputSchema:
    """Build a simplify approval event."""
    is_en = _is_en(language)
    preview = "\n".join(
        f"- **{action.get('action', '?')}** `{action.get('record_id', '?')}`: {action.get('reason', '')}"
        for action in actions[:10]
    )
    return OutputSchema(
        type="chat.ask_user_question",
        index=0,
        payload={
            "request_id": request_id,
            "questions": [
                {
                    "question": (
                        (
                            f"**Simplify evolution experiences for Skill '{skill_name}'**\n\n"
                            f"{len(actions)} action(s):\n{preview}\n\n"
                            "Do you want to execute them?"
                        )
                        if is_en else
                        (
                            f"**精简 Skill '{skill_name}' 的演进经验**\n\n"
                            f"共 {len(actions)} 项操作：\n{preview}\n\n"
                            "是否执行？"
                        )
                    ),
                    "header": "Skill Simplify Approval" if is_en else "Skill 精简审批",
                    "options": (
                        [
                            {"label": "Execute", "description": "Run the simplify actions"},
                            {"label": "Cancel", "description": "Discard this simplify request"},
                        ]
                        if is_en else
                        [
                            {"label": "执行", "description": "执行精简操作"},
                            {"label": "取消", "description": "放弃本次精简"},
                        ]
                    ),
                    "multi_select": False,
                }
            ],
        },
    )


def _build_team_skill_experience_question_event(
    *,
    skill_name: str,
    request_id: str,
    questions: Iterable[Dict[str, str]],
    language: str,
) -> OutputSchema:
    """Build a team skill experience approval event from normalized question inputs."""
    is_en = _is_en(language)
    question_payload = []
    for question in questions:
        section = question["section"]
        content = question["content"]
        question_payload.append(
            {
                "question": (
                    (
                        f"**Team Skill '{skill_name}' evolution:**\n\n"
                        f"- **Section**: {section}\n\n"
                        f"{content[:1000]}"
                    )
                    if is_en else
                    (
                        f"**团队技能 '{skill_name}' 生成了演进经验：**\n\n"
                        f"- **章节**: {section}\n\n"
                        f"{content[:1000]}"
                    )
                ),
                "header": "Team Skill Evolution Approval" if is_en else "团队技能演进审批",
                "options": (
                    [
                        {"label": "Accept", "description": "Keep this evolution"},
                        {"label": "Reject", "description": "Discard this evolution"},
                    ]
                    if is_en else
                    [
                        {"label": "接收", "description": "保留此演进经验"},
                        {"label": "拒绝", "description": "丢弃此演进经验"},
                    ]
                ),
                "multi_select": False,
            }
        )

    return OutputSchema(
        type="chat.ask_user_question",
        index=0,
        payload={
            "request_id": request_id,
            "_evolution_meta": EvolutionHostEventMeta(
                event_kind="approval",
                skill_name=skill_name,
                request_id=request_id,
            ).to_payload(),
            "questions": question_payload,
        },
    )


def build_team_skill_approval_event_from_records(
    skill_name: str,
    request_id: str,
    records: Iterable[EvolutionRecord],
    language: str = "en",
) -> OutputSchema:
    """Build a team skill approval event from staged records."""
    return _build_team_skill_experience_question_event(
        skill_name=skill_name,
        request_id=request_id,
        questions=(
            {"section": record.change.section, "content": record.change.content}
            for record in records
        ),
        language=language,
    )
