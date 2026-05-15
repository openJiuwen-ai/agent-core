# coding: utf-8
"""Tests for shared update execution helpers."""

from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue
from openjiuwen.agent_evolving.update_execution import (
    apply_updates,
    execute_updates,
    summarize_apply_results,
)
from openjiuwen.core.operator.base import Operator


class _RecordingOperator(Operator):
    def __init__(self, operator_id: str):
        self._operator_id = operator_id
        self.calls = []
        self._state = {}

    @property
    def operator_id(self) -> str:
        return self._operator_id

    def get_tunables(self):
        return {}

    def get_state(self):
        return dict(self._state)

    def set_parameter(self, target, value):
        self.calls.append((target, value))
        self._state[target] = value

    def load_state(self, state):
        return None


class TestApplyHelpers:
    @staticmethod
    def test_execute_updates_normalizes_legacy_replace_updates():
        operator = _RecordingOperator("op1")

        results = execute_updates({"op1": operator}, {("op1", "system_prompt"): "new prompt"})

        assert operator.calls == [("system_prompt", "new prompt")]
        assert results == [
            ApplyResult(
                operator_id="op1",
                target="system_prompt",
                applied=True,
                value="new prompt",
            )
        ]

    @staticmethod
    def test_apply_updates_normalizes_legacy_replace_updates():
        operator = _RecordingOperator("op1")

        results = apply_updates({"op1": operator}, {("op1", "system_prompt"): "new prompt"})

        assert operator.calls == [("system_prompt", "new prompt")]
        assert results == [
            ApplyResult(
                operator_id="op1",
                target="system_prompt",
                applied=True,
                value="new prompt",
            )
        ]

    @staticmethod
    def test_apply_updates_supports_mixed_legacy_and_structured_batches():
        operator = _RecordingOperator("op1")

        results = apply_updates(
            {"op1": operator},
            {
                ("op1", "system_prompt"): "prompt",
                ("op1", "experiences"): UpdateValue(
                    payload=["record-1"],
                    mode="append",
                    effect="pending_change",
                    change_type="skill_experience_entry",
                    metadata={"change_type": "skill_experience_entry"},
                ),
            },
        )

        assert operator.calls == [("system_prompt", "prompt")]
        assert results[0].applied is True
        assert results[1].applied is False
        assert results[1].mode == "append"
        assert results[1].effect == "pending_change"

    @staticmethod
    def test_apply_updates_reports_missing_operators_and_none_values_without_crashing():
        operator = _RecordingOperator("op1")

        results = apply_updates(
            {"op1": operator},
            {
                ("missing", "param"): "value",
                ("op1", "param"): None,
            },
        )

        assert operator.calls == []
        assert results[0].operator_id == "missing"
        assert results[0].applied is False
        assert results[1].operator_id == "op1"
        assert results[1].errors == ["update value is None"]

    @staticmethod
    def test_apply_updates_returns_failed_apply_result_from_operator():
        class _FailingOperator(_RecordingOperator):
            def apply_update(self, target, update):
                return ApplyResult(
                    operator_id=self.operator_id,
                    target=target,
                    applied=False,
                    mode=update.mode,
                    effect=update.effect,
                    value=update.payload,
                    errors=["apply failed"],
                )

        operator = _FailingOperator("op1")

        results = apply_updates({"op1": operator}, {("op1", "system_prompt"): "new prompt"})

        assert results == [
            ApplyResult(
                operator_id="op1",
                target="system_prompt",
                applied=False,
                mode="replace",
                effect="state",
                value="new prompt",
                errors=["apply failed"],
            )
        ]

    @staticmethod
    def test_summarize_apply_results_counts_applied_and_failed_updates():
        summary = summarize_apply_results(
            [
                ApplyResult(operator_id="op1", target="a", applied=True),
                ApplyResult(operator_id="op1", target="b", applied=False, errors=["bad update"]),
            ]
        )

        assert summary["total"] == 2
        assert summary["applied"] == 1
        assert summary["failed"] == 1
