# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Repeat-tool notice/steering must report both member_name and tool_name."""
from __future__ import annotations

from openjiuwen.harness.agent_ras.models import Anomaly, AnomalyKind, Severity
from openjiuwen.harness.agent_ras.recovery.robustness_prompt import (
    _repeat_tool_fields,
    steer_text_for,
    user_warning_text_for,
)


def _generic_anomaly(
    *,
    member_name: str = "main_agent",
    tool_name: str | None = "read_file",
    summary: str = "repeat_tool_call on read_file",
    count: int = 5,
) -> Anomaly:
    evidence: dict = {
        "detector_kind": "generic_repeat",
        "msg_key": "generic_repeat",
        "count": count,
        "tool_arguments": '{"file_path": "/tmp/cb_test.txt"}',
    }
    if tool_name is not None:
        evidence["tool_name"] = tool_name
    return Anomaly(
        detector="repeat_tool_call",
        kind=AnomalyKind.REPEAT_TOOL_CALL,
        severity=Severity.LOW,
        member_name=member_name,
        summary=summary,
        evidence=evidence,
    )


class TestRepeatToolFields:
    def test_fields_include_member_and_tool(self):
        fields = _repeat_tool_fields(_generic_anomaly())
        assert fields["member_name"] == "main_agent"
        assert fields["tool_name"] == "read_file"
        assert fields["count"] == 5

    def test_fields_fallback_from_summary_when_evidence_missing_tool(self):
        anomaly = _generic_anomaly(tool_name=None, summary="repeat_tool_call on read_file")
        fields = _repeat_tool_fields(anomaly)
        assert fields["member_name"] == "main_agent"
        assert fields["tool_name"] == "read_file"


class TestRepeatToolPromptRender:
    def test_user_notice_reports_both(self):
        text = user_warning_text_for(_generic_anomaly(), locale="cn")
        assert text is not None
        assert "main_agent" in text
        assert "read_file" in text
        assert "5" in text
        assert "成员" not in text
        # must not pretend the agent name is the tool alone
        assert "工具 main_agent 已重复调用" not in text

    def test_steering_reports_both(self):
        text = steer_text_for(_generic_anomaly(), locale="cn")
        assert text is not None
        assert "- Agent：main_agent" in text
        assert "- 工具：read_file" in text
        assert "main_agent" in text
        assert "read_file" in text
        assert "成员" not in text

    def test_en_user_notice_reports_both(self):
        text = user_warning_text_for(_generic_anomaly(), locale="en")
        assert text is not None
        assert "main_agent" in text
        assert "read_file" in text
