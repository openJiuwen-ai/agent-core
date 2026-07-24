# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for experience draft parsing fallbacks."""

from __future__ import annotations

from openjiuwen.agent_evolving.optimizer.skill_call.experience_draft_parser import (
    SUMMARY_MAX_CHARS,
    normalize_root_cause,
    normalize_summary,
    parse_experience_draft,
)
from openjiuwen.agent_evolving.signal.base import EvolutionTarget


class TestParseExperienceDraftFallbacks:
    @staticmethod
    def test_invalid_section_falls_back_to_troubleshooting(caplog):
        draft = parse_experience_draft(
            {
                "action": "append",
                "section": "Unknown",
                "target": "body",
                "content": "fix it",
            }
        )
        assert draft is not None
        assert draft.patch.section == "Troubleshooting"
        assert "invalid section" in caplog.text
        assert "Unknown" in caplog.text

    @staticmethod
    def test_invalid_target_falls_back_to_body(caplog):
        draft = parse_experience_draft(
            {
                "action": "append",
                "section": "Instructions",
                "target": "invalid",
                "content": "fix it",
            }
        )
        assert draft is not None
        assert draft.patch.target == EvolutionTarget.BODY
        assert "invalid target" in caplog.text
        assert "invalid" in caplog.text


class TestSummaryAndRootCause:
    @staticmethod
    def test_normalize_summary_caps_at_100_chars():
        long_text = "字" * 150
        summary = normalize_summary(long_text)
        assert summary is not None
        assert len(summary) == SUMMARY_MAX_CHARS

    @staticmethod
    def test_parse_draft_keeps_summary_and_root_cause():
        draft = parse_experience_draft(
            {
                "action": "append",
                "section": "Troubleshooting",
                "target": "body",
                "summary": "遇到超时先重试再切换备用接口。",
                "keywords": ["timeout", "retry"],
                "root_cause": "技能缺少超时重试指引，工具失败后直接放弃",
                "content": "## Fix\n- retry",
            }
        )
        assert draft is not None
        assert draft.summary == "遇到超时先重试再切换备用接口。"
        assert draft.root_cause == "技能缺少超时重试指引，工具失败后直接放弃"

    @staticmethod
    def test_normalize_root_cause_flattens_legacy_list():
        cause = normalize_root_cause([
            {
                "failure_type": "external_env",
                "confidence": "0.5",
                "evidence": "net",
                "should_evolve": False,
            },
            {"confidence": 0.9},
            123,
            "权限不足",
        ])
        assert cause == "external_env：net；权限不足"
