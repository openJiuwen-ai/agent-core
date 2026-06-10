# coding: utf-8
"""Tests for SkillDocumentOperator."""

from unittest.mock import MagicMock

from openjiuwen.agent_evolving.protocols import SKILL_CONTENT_TARGET
from openjiuwen.agent_evolving.types import UpdateValue
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


INITIAL_CONTENT = "# My Skill\n\n## Instructions\nDo things."


class TestOperatorId:
    @staticmethod
    def test_format():
        op = SkillDocumentOperator("fund_advisor")
        assert op.operator_id == "skill_document_fund_advisor"

    @staticmethod
    def test_different_names():
        op1 = SkillDocumentOperator("alpha")
        op2 = SkillDocumentOperator("beta")
        assert op1.operator_id != op2.operator_id


class TestTunables:
    @staticmethod
    def test_single_tunable():
        op = SkillDocumentOperator("test")
        tunables = op.get_tunables()
        assert len(tunables) == 1
        assert SKILL_CONTENT_TARGET in tunables

    @staticmethod
    def test_tunable_spec():
        op = SkillDocumentOperator("test")
        spec = op.get_tunables()[SKILL_CONTENT_TARGET]
        assert spec.name == SKILL_CONTENT_TARGET
        assert spec.kind == "text"


class TestSetParameter:
    @staticmethod
    def test_updates_content():
        op = SkillDocumentOperator("test", initial_content="old")
        op.set_parameter(SKILL_CONTENT_TARGET, "new")
        assert op.get_state()["skill_content"] == "new"

    @staticmethod
    def test_triggers_callback():
        cb = MagicMock()
        op = SkillDocumentOperator("test", on_parameter_updated=cb)
        op.set_parameter(SKILL_CONTENT_TARGET, "value")
        cb.assert_called_once_with(SKILL_CONTENT_TARGET, "value")

    @staticmethod
    def test_ignores_unknown_target():
        op = SkillDocumentOperator("test", initial_content="keep")
        op.set_parameter("unknown_target", "bad")
        assert op.get_state()["skill_content"] == "keep"

    @staticmethod
    def test_none_value_becomes_empty():
        op = SkillDocumentOperator("test", initial_content="content")
        op.set_parameter(SKILL_CONTENT_TARGET, None)
        assert op.get_state()["skill_content"] == ""


class TestPreviewUpdate:
    @staticmethod
    def test_valid_target():
        op = SkillDocumentOperator("test")
        update = UpdateValue(payload="new content")
        result = op.preview_update(SKILL_CONTENT_TARGET, update)
        assert result.applied is True
        assert result.operator_id == op.operator_id

    @staticmethod
    def test_invalid_target():
        op = SkillDocumentOperator("test")
        update = UpdateValue(payload="bad")
        result = op.preview_update("wrong_target", update)
        assert result.applied is False
        assert len(result.errors) > 0


class TestState:
    @staticmethod
    def test_get_state():
        op = SkillDocumentOperator("test", initial_content=INITIAL_CONTENT)
        state = op.get_state()
        assert state == {"skill_content": INITIAL_CONTENT}

    @staticmethod
    def test_load_state_restores_content():
        op = SkillDocumentOperator("test")
        op.load_state({"skill_content": "restored"})
        assert op.get_state()["skill_content"] == "restored"

    @staticmethod
    def test_load_state_triggers_callback():
        cb = MagicMock()
        op = SkillDocumentOperator("test", on_parameter_updated=cb)
        op.load_state({"skill_content": "restored"})
        cb.assert_called_once_with(SKILL_CONTENT_TARGET, "restored")

    @staticmethod
    def test_load_state_missing_key_defaults_empty():
        op = SkillDocumentOperator("test", initial_content="keep")
        op.load_state({})
        assert op.get_state()["skill_content"] == ""

    @staticmethod
    def test_roundtrip():
        op1 = SkillDocumentOperator("test", initial_content=INITIAL_CONTENT)
        state = op1.get_state()
        op2 = SkillDocumentOperator("test")
        op2.load_state(state)
        assert op2.get_state() == op1.get_state()


class TestNotSkillExperienceOperator:
    @staticmethod
    def test_does_not_inherit_skill_experience():
        from openjiuwen.core.operator.skill_call.base import SkillExperienceOperator

        op = SkillDocumentOperator("test")
        assert not isinstance(op, SkillExperienceOperator)
