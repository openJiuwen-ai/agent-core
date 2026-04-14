# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for online signal detector."""

from __future__ import annotations

import json
import re

from openjiuwen.agent_evolving.online.schema import EvolutionCategory, EvolutionSignal
from openjiuwen.agent_evolving.online.signal_detector import (
    SignalDetector,
    _extract_around_match,
    _get_field,
    make_signal_fingerprint,
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
        assert detector._detect_skill_from_tool_calls(tool_calls) is None

    @staticmethod
    def test_deduplicate_by_four_tuple_fingerprint():
        detector = SignalDetector()
        messages = [
            {"role": "tool", "content": "Error happened in command A"},
            {"role": "tool", "content": "Error happened in command A"},
        ]
        signals = detector.detect(messages)
        assert len(signals) == 1

    @staticmethod
    def test_different_tool_names_not_deduped():
        detector = SignalDetector()
        messages = [
            {"role": "tool", "name": "bash", "content": "Error happened"},
            {"role": "tool", "name": "code", "content": "Error happened"},
        ]
        signals = detector.detect(messages)
        assert len(signals) == 2

    @staticmethod
    def test_script_artifact_detected_on_successful_code_exec():
        detector = SignalDetector()
        code_snippet = "import matplotlib\n# generate chart with many lines of code here"
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "tc_1",
                    "name": "code",
                    "arguments": json.dumps({"code": code_snippet}),
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "content": "Chart saved to output.png",
            },
        ]
        signals = detector.detect(messages)
        assert any(s.signal_type == "script_artifact" for s in signals)
        script_sig = [s for s in signals if s.signal_type == "script_artifact"][0]
        assert script_sig.section == "Scripts"
        assert code_snippet[:200] in script_sig.excerpt

    @staticmethod
    def test_no_script_artifact_when_execution_fails():
        detector = SignalDetector()
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "tc_2",
                    "name": "code",
                    "arguments": json.dumps({"code": "x = 1 / 0  # this will produce a long error traceback"}),
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_2",
                "content": "Traceback (most recent call last): ZeroDivisionError",
            },
        ]
        signals = detector.detect(messages)
        assert not any(s.signal_type == "script_artifact" for s in signals)
        assert any(s.signal_type == "execution_failure" for s in signals)

    @staticmethod
    def test_data_fetch_tool_content_not_flagged_as_failure():
        detector = SignalDetector()
        messages = [
            {
                "role": "tool",
                "name": "web_search",
                "content": "Search results: error handling in Python ... timeout patterns ...",
            },
        ]
        signals = detector.detect(messages)
        assert len(signals) == 0

    @staticmethod
    def test_resolve_active_skill_picks_most_recent():
        history = [(0, "skill-a"), (3, "skill-b")]
        assert SignalDetector._resolve_active_skill(4, history) == "skill-b"
        assert SignalDetector._resolve_active_skill(2, history) == "skill-a"
        assert SignalDetector._resolve_active_skill(0, history) == "skill-a"

    @staticmethod
    def test_resolve_active_skill_returns_none_when_empty():
        assert SignalDetector._resolve_active_skill(5, []) is None

    @staticmethod
    def test_extract_code_from_args_json_string():
        tc = {"arguments": json.dumps({"code": "print('hello world, this is a long script')"})}
        result = SignalDetector._extract_code_from_args(tc)
        assert "hello world" in result

    @staticmethod
    def test_extract_code_from_args_dict_arguments():
        tc = {"arguments": {"command": "ls -la /home/user/very/long/path/foo/bar"}}
        result = SignalDetector._extract_code_from_args(tc)
        assert "ls -la" in result

    @staticmethod
    def test_extract_code_from_args_short_code_ignored():
        tc = {"arguments": json.dumps({"code": "x = 1"})}
        assert SignalDetector._extract_code_from_args(tc) == ""

    @staticmethod
    def test_extract_code_from_args_invalid_json():
        tc = {"arguments": "not json"}
        assert SignalDetector._extract_code_from_args(tc) == ""

    @staticmethod
    def test_tool_name_recovery_from_tool_call_id():
        detector = SignalDetector()
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_abc", "name": "bash", "arguments": "{}"}],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "content": "Error: command failed with timeout",
            },
        ]
        signals = detector.detect(messages)
        assert len(signals) == 1
        assert signals[0].tool_name == "bash"

    @staticmethod
    def test_user_correction_gets_active_skill():
        detector = SignalDetector(existing_skills={"my-skill"})
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"name": "read_file", "arguments": "/skills/my-skill/SKILL.md"},
                ],
            },
            {"role": "user", "content": "不对，应该先读取配置再执行"},
        ]
        signals = detector.detect(messages)
        assert len(signals) == 1
        assert signals[0].skill_name == "my-skill"


class TestMakeSignalFingerprint:
    @staticmethod
    def test_fingerprint_tuple_structure():
        signal = EvolutionSignal(
            signal_type="execution_failure",
            evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
            section="Troubleshooting",
            excerpt="a" * 300,
            tool_name="bash",
            skill_name="skill-a",
        )
        fp = make_signal_fingerprint(signal)
        assert fp == ("execution_failure", "bash", "skill-a", "a" * 200)

    @staticmethod
    def test_fingerprint_none_fields_become_empty_string():
        signal = EvolutionSignal(
            signal_type="user_correction",
            evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
            section="Examples",
            excerpt="short",
        )
        fp = make_signal_fingerprint(signal)
        assert fp == ("user_correction", "", "", "short")


class TestGetField:
    @staticmethod
    def test_dict_access():
        assert _get_field({"a": 1}, "a") == 1
        assert _get_field({"a": 1}, "b", "default") == "default"

    @staticmethod
    def test_object_access():
        from types import SimpleNamespace
        obj = SimpleNamespace(x=42)
        assert _get_field(obj, "x") == 42
        assert _get_field(obj, "y", "nope") == "nope"
