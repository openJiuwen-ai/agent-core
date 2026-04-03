# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for online signal detector."""

from __future__ import annotations

import re

from openjiuwen.agent_evolving.online.signal_detector import (
    SignalDetector,
    _extract_around_match,
)


class TestExtractAroundMatch:
    @staticmethod
    def test_extract_around_match_clamps_boundaries():
        text = "abc ERROR xyz"
        match = re.search("ERROR", text)
        excerpt = _extract_around_match(text, match, before=100, after=100)
        assert excerpt == text


class TestSignalDetector:
    @staticmethod
    def test_detect_execution_failure_and_skill_from_tool_calls():
        detector = SignalDetector(existing_skills={"invoice-parser"})
        messages = [
            {
                "role": "assistant",
                "content": "reading file",
                "tool_calls": [
                    {
                        "name": "read_file",
                        "arguments": r'{"file_path":"C:\\skills\\invoice-parser\\SKILL.md"}',
                    }
                ],
            },
            {
                "role": "tool",
                "name": "bash",
                "content": "Traceback: command failed with timeout",
            },
        ]

        signals = detector.detect(messages)
        assert len(signals) == 1
        assert signals[0].signal_type == "execution_failure"
        assert signals[0].skill_name == "invoice-parser"
        assert signals[0].section == "Troubleshooting"

    @staticmethod
    def test_detect_user_correction_signal():
        detector = SignalDetector()
        messages = [
            {"role": "user", "content": "不对，应该先读取配置再执行"},
        ]
        signals = detector.detect(messages)
        assert len(signals) == 1
        assert signals[0].signal_type == "user_correction"
        assert signals[0].section == "Examples"

    @staticmethod
    def test_skip_tool_schema_like_content():
        detector = SignalDetector()
        messages = [
            {
                "role": "tool",
                "name": "read_file",
                "content": "{'content': '---\\nname: x\\ndescription: y'} error",
            }
        ]
        assert detector.detect(messages) == []

    @staticmethod
    def test_skill_detection_respects_existing_skills_filter():
        detector = SignalDetector(existing_skills={"skill-a"})
        tool_calls = [
            {"name": "read_file", "arguments": "/root/skill-b/SKILL.md"},
        ]
        assert detector._detect_skill_from_tool_calls(tool_calls, None) is None

    @staticmethod
    def test_deduplicate_by_excerpt_prefix():
        detector = SignalDetector()
        messages = [
            {"role": "tool", "content": "Error happened in command A"},
            {"role": "tool", "content": "Error happened in command A"},
        ]
        signals = detector.detect(messages)
        assert len(signals) == 1
