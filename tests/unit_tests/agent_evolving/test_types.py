# coding: utf-8
"""Tests for shared evolution apply types."""

from openjiuwen.agent_evolving import types as evolution_types
from openjiuwen.agent_evolving.experience.types import PendingChange

from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue
from openjiuwen.agent_evolving.types import normalize_updates


class TestUpdateValueCompatibility:
    """Compatibility behavior for legacy and structured updates."""

    @staticmethod
    def test_normalize_updates_wraps_legacy_values_as_replace_state():
        normalized = normalize_updates(
            {
                ("op1", "system_prompt"): "new prompt",
                ("op2", "temperature"): 0.3,
            }
        )

        assert normalized[("op1", "system_prompt")] == UpdateValue(payload="new prompt")
        assert normalized[("op2", "temperature")] == UpdateValue(payload=0.3)

    @staticmethod
    def test_normalize_updates_preserves_structured_values():
        structured = UpdateValue(
            payload={"records": [1, 2]},
            mode="append",
            effect="pending_change",
            metadata={"change_type": "experience_entry"},
        )

        normalized = normalize_updates({("skill_op", "experience"): structured})

        assert normalized[("skill_op", "experience")] is structured

    @staticmethod
    def test_normalize_updates_supports_mixed_legacy_and_structured_values():
        structured = UpdateValue(
            payload={"patch": "delta"},
            mode="merge",
            effect="pending_change",
            metadata={"change_type": "team_skill_patch"},
        )

        normalized = normalize_updates(
            {
                ("op1", "system_prompt"): "new prompt",
                ("op2", "team_skill"): structured,
            }
        )

        assert normalized[("op1", "system_prompt")] == UpdateValue(payload="new prompt")
        assert normalized[("op2", "team_skill")] is structured

    @staticmethod
    def test_normalize_updates_preserves_legacy_skill_experience_semantics():
        legacy_records = ["record-1", "record-2"]

        normalized = normalize_updates({("skill_experience_skill-a", "experiences"): legacy_records})

        assert normalized[("skill_experience_skill-a", "experiences")] == UpdateValue(
            payload=legacy_records,
            mode="append",
            effect="pending_change",
            change_type="skill_experience_entry",
            metadata={"change_type": "skill_experience_entry"},
        )

    @staticmethod
    def test_legacy_update_aliases_remain_compatible():
        assert hasattr(evolution_types, "UpdateKey")
        assert hasattr(evolution_types, "Updates")
        assert not hasattr(evolution_types, "NormalizedUpdates")
        assert not hasattr(evolution_types, "ApplyLifecycleStage")


class TestApplyResult:
    """Shared apply result contract."""

    @staticmethod
    def test_apply_result_ok_depends_on_applied_flag_and_errors():
        result = ApplyResult(operator_id="op1", target="system_prompt", applied=True)

        assert result.ok is True

        failed = ApplyResult(
            operator_id="op1",
            target="system_prompt",
            applied=False,
            errors=["apply failed"],
        )

        assert failed.ok is False

    @staticmethod
    def test_apply_result_exposes_pending_change_fields_for_future_lifecycle():
        result = ApplyResult(
            operator_id="skill_op",
            target="experience",
            applied=True,
            mode="append",
            effect="pending_change",
            records=["record-1"],
            change_type="skill_experience_entry",
            lifecycle_stage="local_apply_completed",
            pending_change_id="pending-123",
        )

        assert result.pending_change_id == "pending-123"
        assert result.effect == "pending_change"
        assert result.records == ["record-1"]
        assert result.change_type == "skill_experience_entry"
        assert result.lifecycle_stage == "local_apply_completed"


class TestPendingChangeCompatibility:
    """Canonical naming for staged pending changes."""

    @staticmethod
    def test_pending_change_make_uses_unified_skill_experience_entry_name():
        pending = PendingChange.make("skill-a", [])

        assert pending.operator_id == "skill_experience_skill-a"
        assert pending.skill_name == "skill-a"
        assert pending.change_type == "skill_experience_entry"
