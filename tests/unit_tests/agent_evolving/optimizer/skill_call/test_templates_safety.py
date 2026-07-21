# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contract tests for the safety enhancements in skill evolution prompts.

These are deterministic string/format assertions (safe for CI). Behavioral
checks (whether a model actually refuses an injected sample) are non-deterministic
and belong to offline eval, not CI.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.optimizer.skill_call.templates import (
    JSON_FIX_PROMPT,
    JSON_FIX_PROMPT_STRICT,
    SKILL_EXPERIENCE_ANALYZER_PROMPT,
    SKILL_EXPERIENCE_FORMATTER_PROMPT,
    SKILL_EXPERIENCE_GENERATE_PROMPT,
)

_GEN_INPUTS = {
    "skill_content": "s",
    "signals_json": "[]",
    "tool_call_chain": "t",
    "conversation_snippet": "c",
    "existing_desc_summary": "d",
    "existing_body_summary": "b",
    "user_query": "u",
}

_LANGS = ("cn", "en")


class TestTemplatesFormatSafely:
    @staticmethod
    @pytest.mark.parametrize("lang", _LANGS)
    def test_generate_and_analyzer_format_without_error(lang: str):
        # Must not raise KeyError/IndexError/ValueError from stray braces.
        SKILL_EXPERIENCE_GENERATE_PROMPT[lang].format(**_GEN_INPUTS)
        SKILL_EXPERIENCE_ANALYZER_PROMPT[lang].format(**_GEN_INPUTS)

    @staticmethod
    @pytest.mark.parametrize("lang", _LANGS)
    def test_formatter_formats_without_error(lang: str):
        SKILL_EXPERIENCE_FORMATTER_PROMPT[lang].format(analyzer_output="x")

    @staticmethod
    def test_json_fix_prompts_format_without_error():
        JSON_FIX_PROMPT.format(parse_error="e", broken_output="o")
        JSON_FIX_PROMPT_STRICT.format(parse_error="e", broken_preview="p")


class TestAnalyzerSafetyContract:
    @staticmethod
    @pytest.mark.parametrize("lang", _LANGS)
    def test_new_failure_types_present(lang: str):
        prompt = SKILL_EXPERIENCE_ANALYZER_PROMPT[lang]
        assert "policy_violation" in prompt
        assert "prompt_injection" in prompt

    @staticmethod
    @pytest.mark.parametrize("lang", _LANGS)
    def test_safety_section_and_untrusted_fields_present(lang: str):
        prompt = SKILL_EXPERIENCE_ANALYZER_PROMPT[lang]
        # user_query and skill_content must be declared untrusted (injection channel).
        assert "user_query" in prompt
        assert "skill_content" in prompt


class TestGenerateSafetyContract:
    @staticmethod
    @pytest.mark.parametrize("lang", _LANGS)
    def test_step_zero_and_unsafe_skip_present(lang: str):
        prompt = SKILL_EXPERIENCE_GENERATE_PROMPT[lang]
        assert "unsafe" in prompt
        assert "user_query" in prompt
        assert "skill_content" in prompt

    @staticmethod
    @pytest.mark.parametrize("lang", _LANGS)
    def test_unsafe_skip_survives_format(lang: str):
        # Doubled braces must render to a single valid JSON snippet after format.
        rendered = SKILL_EXPERIENCE_GENERATE_PROMPT[lang].format(**_GEN_INPUTS)
        assert '{"action": "skip", "skip_reason": "unsafe"}' in rendered


class TestFormatterSafetyContract:
    @staticmethod
    @pytest.mark.parametrize("lang", _LANGS)
    def test_unsafe_enum_and_fallback_present(lang: str):
        prompt = SKILL_EXPERIENCE_FORMATTER_PROMPT[lang]
        assert "unsafe" in prompt

    @staticmethod
    @pytest.mark.parametrize("lang", _LANGS)
    def test_unsafe_skip_survives_format(lang: str):
        rendered = SKILL_EXPERIENCE_FORMATTER_PROMPT[lang].format(analyzer_output="x")
        assert '{"action": "skip", "skip_reason": "unsafe"}' in rendered


class TestJsonFixSafetyContract:
    @staticmethod
    def test_both_fix_prompts_declare_data_only():
        for prompt in (JSON_FIX_PROMPT, JSON_FIX_PROMPT_STRICT):
            assert "绝不服从" in prompt

    @staticmethod
    def test_json_fix_prompt_lists_unsafe_in_skip_reason_enum():
        assert "unsafe" in JSON_FIX_PROMPT
        assert (
            "irrelevant | duplicate | low_priority | unsafe"
            in JSON_FIX_PROMPT
        )

    @staticmethod
    def test_json_fix_strict_example_includes_unsafe_skip():
        rendered = JSON_FIX_PROMPT_STRICT.format(
            parse_error="e",
            broken_preview="p",
        )
        assert '"skip_reason":"unsafe"' in rendered
