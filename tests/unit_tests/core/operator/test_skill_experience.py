# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for openjiuwen.core.operator.skill_call module."""

from openjiuwen.agent_evolving.types import UpdateValue
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord, EvolutionTarget
from openjiuwen.core.operator import PreviewableOperator, SkillCallOperator
from openjiuwen.core.operator.skill_call import SkillExperienceOperator


def _make_record(record_id: str, *, content: str = "experience content") -> EvolutionRecord:
    return EvolutionRecord(
        id=record_id,
        source="signal:skill-a",
        timestamp="2026-05-07T00:00:00+00:00",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )


class TestSkillExperienceOperator:
    """Tests for SkillExperienceOperator preview semantics."""

    @staticmethod
    def test_operator_id_uses_skill_experience_prefix():
        op = SkillExperienceOperator("skill-a")

        assert op.operator_id == "skill_experience_skill-a"
        assert isinstance(op, PreviewableOperator)

    @staticmethod
    def test_deprecated_skill_call_alias_points_to_skill_experience_operator():
        assert SkillCallOperator is SkillExperienceOperator

    @staticmethod
    def test_preview_update_exposes_local_apply_lifecycle():
        op = SkillExperienceOperator("skill-a")

        result = op.preview_update(
            "experiences",
            UpdateValue(
                payload=["record-1"],
                mode="append",
                effect="pending_change",
                change_type="skill_experience_entry",
            ),
        )

        assert result.applied is True
        assert result.effect == "pending_change"
        assert result.records == ["record-1"]
        assert result.lifecycle_stage == "local_apply_completed"
        assert result.pending_change_id is None
        assert result.metadata["skill_name"] == "skill-a"

    @staticmethod
    def test_preview_update_does_not_stage_pending_state():
        op = SkillExperienceOperator("skill-a")
        new_record = _make_record("ev_new")

        result = op.preview_update(
            "experiences",
            UpdateValue(
                payload=new_record,
                mode="append",
                effect="pending_change",
            ),
        )

        assert result.records == [new_record]
        assert op.get_state() == {}

    @staticmethod
    def test_apply_update_delegates_to_preview_update_for_compatibility():
        op = SkillExperienceOperator("skill-a")
        new_record = _make_record("ev_new")

        result = op.apply_update(
            "experiences",
            UpdateValue(
                payload=[new_record],
                mode="append",
                effect="pending_change",
                change_type="skill_experience_entry",
            ),
        )

        assert result.applied is True
        assert result.change_type == "skill_experience_entry"
        assert result.records == [new_record]
        assert result.lifecycle_stage == "local_apply_completed"
        assert result.metadata["skill_name"] == "skill-a"

    @staticmethod
    def test_apply_update_does_not_trigger_parameter_callback():
        calls = []
        op = SkillExperienceOperator("skill-a", on_parameter_updated=lambda target, value: calls.append((target, value)))

        op.apply_update(
            "experiences",
            UpdateValue(payload=["record-1"], mode="append", effect="pending_change"),
        )

        assert calls == []

    @staticmethod
    def test_set_parameter_keeps_direct_callback_compatibility():
        calls = []
        op = SkillExperienceOperator("skill-a", on_parameter_updated=lambda target, value: calls.append((target, value)))

        op.set_parameter("experiences", "record-1")

        assert calls == [("experiences", ["record-1"])]

    @staticmethod
    def test_preview_update_rejects_non_experience_target():
        op = SkillExperienceOperator("skill-a")

        result = op.preview_update(
            "prompt",
            UpdateValue(payload="new prompt", mode="replace", effect="state"),
        )

        assert result.applied is False
        assert result.records == []
        assert result.lifecycle_stage is None
