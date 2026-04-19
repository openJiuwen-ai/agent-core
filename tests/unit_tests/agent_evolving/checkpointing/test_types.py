# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for checkpointing types (EvolutionPatch, EvolutionRecord, EvolutionLog)."""

from __future__ import annotations

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionLog,
    EvolutionPatch,
    EvolutionRecord,
)
from openjiuwen.agent_evolving.signal.base import (
    EvolutionCategory,
    EvolutionSignal,
    EvolutionTarget,
)


def make_patch(target: EvolutionTarget = EvolutionTarget.BODY, **kwargs) -> EvolutionPatch:
    data = {
        "section": "Troubleshooting",
        "action": "append",
        "content": "use fallback",
        "target": target,
    }
    data.update(kwargs)
    return EvolutionPatch(**data)


def make_record(**kwargs) -> EvolutionRecord:
    patch = kwargs.pop("change", make_patch())
    data = {
        "id": "ev_00112233",
        "source": "execution_failure",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "context": "ctx",
        "change": patch,
        "applied": False,
    }
    data.update(kwargs)
    return EvolutionRecord(**data)


class TestEvolutionPatch:
    @staticmethod
    def test_to_dict_includes_optional_fields():
        patch = make_patch(skip_reason="duplicate", merge_target="ev_xxx")
        data = patch.to_dict()
        assert data["skip_reason"] == "duplicate"
        assert data["merge_target"] == "ev_xxx"
        assert data["target"] == "body"

    @staticmethod
    def test_to_dict_includes_script_fields():
        patch = make_patch(
            target=EvolutionTarget.SCRIPT,
            section="Scripts",
            script_filename="gen_chart.py",
            script_language="python",
            script_purpose="generate bar chart",
        )
        data = patch.to_dict()
        assert data["target"] == "script"
        assert data["script_filename"] == "gen_chart.py"
        assert data["script_language"] == "python"
        assert data["script_purpose"] == "generate bar chart"

    @staticmethod
    def test_to_dict_omits_none_script_fields():
        patch = make_patch()
        data = patch.to_dict()
        assert "script_filename" not in data
        assert "script_language" not in data
        assert "script_purpose" not in data

    @staticmethod
    def test_from_dict_with_script_target():
        patch = EvolutionPatch.from_dict({
            "section": "Scripts",
            "action": "append",
            "content": "print('hello')",
            "target": "script",
            "script_filename": "hello.py",
            "script_language": "python",
            "script_purpose": "demo",
        })
        assert patch.target == EvolutionTarget.SCRIPT
        assert patch.script_filename == "hello.py"
        assert patch.script_language == "python"
        assert patch.script_purpose == "demo"

    @staticmethod
    def test_from_dict_fallback_target():
        patch = EvolutionPatch.from_dict(
            {
                "section": "Instructions",
                "action": "append",
                "content": "x",
                "target": "invalid-target",
            }
        )
        assert patch.target == EvolutionTarget.BODY


class TestEvolutionRecord:
    @staticmethod
    def test_make_generates_prefixed_id():
        record = EvolutionRecord.make(
            source="tool_failure",
            context="ctx",
            change=make_patch(),
        )
        assert record.id.startswith("ev_")
        assert record.is_pending is True
        assert record.source == "tool_failure"

    @staticmethod
    def test_from_dict_uses_defaults():
        record = EvolutionRecord.from_dict({})
        assert record.id.startswith("ev_")
        assert record.source == "unknown"
        assert record.change.target == EvolutionTarget.BODY
        assert record.applied is False


class TestEvolutionLog:
    @staticmethod
    def test_pending_entries_filters_applied():
        rec_pending = make_record(id="ev_a", applied=False)
        rec_applied = make_record(id="ev_b", applied=True)
        log = EvolutionLog(skill_id="skill-a", entries=[rec_pending, rec_applied])
        assert [item.id for item in log.pending_entries] == ["ev_a"]

    @staticmethod
    def test_round_trip_and_empty():
        original = EvolutionLog(
            skill_id="skill-a",
            version="1.0.0",
            updated_at="2026-01-01T00:00:00+00:00",
            entries=[make_record()],
        )
        data = original.to_dict()
        loaded = EvolutionLog.from_dict(data)
        empty = EvolutionLog.empty("skill-x")

        assert loaded.skill_id == "skill-a"
        assert len(loaded.entries) == 1
        assert empty.skill_id == "skill-x"
        assert empty.entries == []


class TestEvolutionSignal:
    @staticmethod
    def test_to_dict_contains_enum_value():
        signal = EvolutionSignal(
            signal_type="execution_failure",
            evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
            section="Troubleshooting",
            excerpt="timeout",
            tool_name="bash",
            skill_name="skill-a",
        )
        data = signal.to_dict()
        assert data["evolution_type"] == "skill_experience"
        assert data["tool_name"] == "bash"

    @staticmethod
    def test_to_dict_omits_context_when_none():
        """Online signals have no context; key must be absent from serialized dict."""
        signal = EvolutionSignal(
            signal_type="execution_failure",
            evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
            section="Troubleshooting",
            excerpt="timeout",
        )
        assert "context" not in signal.to_dict()

    @staticmethod
    def test_to_dict_includes_context_when_present():
        """Offline signals carry evaluation evidence; context must survive serialization."""
        ctx = {
            "question": "How to deploy?",
            "label": "use kubectl",
            "answer": "wrong answer",
            "reason": "missed kubectl",
            "score": 0.0,
        }
        signal = EvolutionSignal(
            signal_type="low_score",
            evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
            section="Troubleshooting",
            excerpt="score=0.00",
            skill_name="skill-a",
            context=ctx,
        )
        data = signal.to_dict()
        assert "context" in data
        assert data["context"]["question"] == "How to deploy?"
        assert data["context"]["score"] == 0.0
