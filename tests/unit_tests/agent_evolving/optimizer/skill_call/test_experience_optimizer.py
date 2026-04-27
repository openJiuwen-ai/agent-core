# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for SkillExperienceOptimizer (skill_call)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionContext,
    EvolutionPatch,
    EvolutionRecord,
)
from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    SKILL_EXPERIENCE_GENERATE_PROMPT,
    SkillExperienceOptimizer,
    _build_conversation_snippet,
    _build_context,
    _extract_json,
    _fix_json_text,
    _looks_truncated,
    _preview_section,
    _split_into_sections,
    _summarize_skill_content,
    _parse_llm_response,
    _parse_single_patch,
)
from openjiuwen.agent_evolving.signal.base import (
    EvolutionCategory,
    EvolutionSignal,
    EvolutionTarget,
)


def make_signal(excerpt: str = "tool timeout") -> EvolutionSignal:
    return EvolutionSignal(
        signal_type="execution_failure",
        evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
        section="Troubleshooting",
        excerpt=excerpt,
        tool_name="bash",
        skill_name="skill-a",
    )


def make_record(record_id: str, content: str = "x") -> EvolutionRecord:
    return EvolutionRecord(
        id=record_id,
        source="execution_failure",
        timestamp="2026-01-01T00:00:00+00:00",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
        applied=False,
    )


class TestConversationSnippet:
    @staticmethod
    def test_build_conversation_snippet_handles_mixed_content():
        messages = [
            {"role": "user", "content": ["line1", {"text": "line2"}]},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "read_file"}, {"name": "bash"}],
            },
        ]
        snippet = _build_conversation_snippet(messages, language="cn")
        assert "[user] line1\nline2" in snippet
        assert "(tool_calls: read_file, bash)" in snippet
        assert "无文本" in snippet

    @staticmethod
    def test_build_conversation_snippet_limits_messages():
        messages = [{"role": "user", "content": f"m{i}"} for i in range(5)]
        snippet = _build_conversation_snippet(messages, max_messages=2, language="en")
        assert "[user] m0" not in snippet
        assert "[user] m3" in snippet
        assert "[user] m4" in snippet


class TestSkillExperienceOptimizerGenerate:
    @staticmethod
    @pytest.mark.asyncio
    async def test_generate_returns_empty_when_no_signals():
        optimizer = SkillExperienceOptimizer(llm=MagicMock(), model="dummy", language="cn")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[],
            skill_content="# skill",
            messages=[],
            existing_desc_records=[],
            existing_body_records=[],
        )
        result = await optimizer.generate_records(ctx)
        assert result == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_generate_returns_empty_on_llm_exception():
        llm = MagicMock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("network failed"))
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[make_signal()],
            skill_content="# skill",
            messages=[{"role": "user", "content": "hello"}],
            existing_desc_records=[],
            existing_body_records=[],
        )
        assert await optimizer.generate_records(ctx) == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_generate_filters_skip_empty_and_truncates_to_two():
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content="""
[
  {"action":"skip","skip_reason":"duplicate"},
  {"action":"append","target":"body","section":"Troubleshooting","content":"A","merge_target":null},
  {"action":"append","target":"description","section":"Instructions","content":"B","merge_target":null},
  {"action":"append","target":"body","section":"Examples","content":"C","merge_target":null},
  {"action":"append","target":"body","section":"Examples","content":"   ","merge_target":null}
]
"""
            )
        )
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="en")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[make_signal("s1"), make_signal("s2")],
            skill_content="# skill",
            messages=[{"role": "user", "content": "hello"}],
            existing_desc_records=[make_record("ev_d1", "desc old")],
            existing_body_records=[make_record("ev_b1", "body old")],
        )
        records = await optimizer.generate_records(ctx)
        assert len(records) == 2
        assert records[0].change.content == "A"
        assert records[1].change.content == "B"

    @staticmethod
    def test_update_llm_updates_runtime_references():
        optimizer = SkillExperienceOptimizer(llm="old", model="m1", language="cn")
        optimizer.update_llm(llm="new", model="m2")
        assert optimizer._llm == "new"
        assert optimizer._model == "m2"


class TestParsing:
    @staticmethod
    def test_parse_llm_response_supports_json_codeblock_and_fallback():
        codeblock = """```json
[
  {"action":"append","target":"body","section":"Troubleshooting","content":"A","merge_target":"null"}
]
```"""
        patches = _parse_llm_response(codeblock)
        assert len(patches) == 1
        assert patches[0].merge_target is None

        mixed = 'prefix text {"action":"append","target":"invalid","section":"NotExist","content":"X"} suffix'
        patches2 = _parse_llm_response(mixed)
        assert len(patches2) == 1
        assert patches2[0].section == "Troubleshooting"
        assert patches2[0].target == EvolutionTarget.BODY

    @staticmethod
    def test_parse_llm_response_invalid_returns_none():
        assert _parse_llm_response("not json at all") is None

    @staticmethod
    def test_parse_single_patch_skip():
        patch = _parse_single_patch({"action": "skip", "skip_reason": "irrelevant"})
        assert patch.action == "skip"
        assert patch.skip_reason == "irrelevant"

    @staticmethod
    def test_parse_single_patch_with_script_fields():
        patch = _parse_single_patch(
            {
                "action": "append",
                "target": "script",
                "section": "Scripts",
                "content": "import os",
                "script_filename": "setup.py",
                "script_language": "python",
                "script_purpose": "environment setup",
            }
        )
        assert patch.target == EvolutionTarget.SCRIPT
        assert patch.script_filename == "setup.py"
        assert patch.script_language == "python"
        assert patch.script_purpose == "environment setup"

    @staticmethod
    def test_parse_llm_response_with_trailing_comma():
        raw = '[{"action":"append","target":"body","section":"Troubleshooting","content":"fix",},]'
        patches = _parse_llm_response(raw)
        assert patches is not None
        assert len(patches) == 1

    @staticmethod
    def test_parse_llm_response_with_comments():
        raw = """[
  // this is a comment
  {"action":"append","target":"body","section":"Troubleshooting","content":"fix"}
]"""
        patches = _parse_llm_response(raw)
        assert patches is not None
        assert len(patches) == 1


class TestSummarizeSkillContent:
    @staticmethod
    def test_short_content_unchanged():
        raw = "# Skill\nshort content"
        assert _summarize_skill_content(raw) == raw

    @staticmethod
    def test_long_content_summarized():
        sections = ["# Intro\n" + "a" * 500]
        for i in range(10):
            sections.append(f"## Section {i}\n" + "b" * 1000)
        raw = "\n".join(sections)
        result = _summarize_skill_content(raw, max_chars=2000)
        assert len(result) <= 2100
        assert "# Intro" in result
        assert "## Section 0" in result
        assert "以下章节仅保留标题与开头摘要" in result


class TestSplitIntoSections:
    @staticmethod
    def test_splits_on_headings():
        text = "# A\ncontent a\n## B\ncontent b\n### C\ncontent c"
        sections = _split_into_sections(text)
        assert len(sections) == 3
        assert sections[0].startswith("# A")
        assert sections[1].startswith("## B")

    @staticmethod
    def test_no_headings():
        text = "just plain text\nno headings"
        sections = _split_into_sections(text)
        assert len(sections) == 1


class TestPreviewSection:
    @staticmethod
    def test_short_body_unchanged():
        section = "## Title\nShort body"
        assert _preview_section(section) == section

    @staticmethod
    def test_long_body_truncated():
        section = "## Title\n" + "x" * 500
        result = _preview_section(section, preview_chars=100)
        assert result.startswith("## Title")
        assert result.endswith("...")
        assert len(result) < len(section)

    @staticmethod
    def test_heading_only():
        assert _preview_section("## Empty") == "## Empty"


class TestFixJsonText:
    @staticmethod
    def test_removes_markdown_fences():
        text = '```json\n[{"a": 1}]\n```'
        assert _fix_json_text(text) == '[{"a": 1}]'

    @staticmethod
    def test_removes_comments_and_trailing_commas():
        text = '[{"a": 1}, // comment\n]'
        fixed = _fix_json_text(text)
        assert "//" not in fixed
        import json

        assert json.loads(fixed) == [{"a": 1}]


class TestExtractJson:
    @staticmethod
    def test_direct_parse():
        assert _extract_json("[1, 2]") == [1, 2]

    @staticmethod
    def test_with_markdown_fence():
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    @staticmethod
    def test_embedded_json_extraction():
        raw = 'Some text before [{"action":"append"}] some text after'
        result = _extract_json(raw)
        assert result == [{"action": "append"}]

    @staticmethod
    def test_empty_string():
        assert _extract_json("") is None
        assert _extract_json("   ") is None

    @staticmethod
    def test_completely_broken():
        assert _extract_json("no json here at all!!!") is None


class TestBuildContext:
    @staticmethod
    def test_empty_signals():
        assert _build_context([]) == ""

    @staticmethod
    def test_budget_splitting():
        signals = [
            SimpleNamespace(signal_type="a", excerpt="x" * 1000),
            SimpleNamespace(signal_type="b", excerpt="y" * 1000),
        ]
        result = _build_context(signals, max_chars=500)
        assert "[a]" in result
        assert "[b]" in result
        assert "..." in result

    @staticmethod
    def test_short_signals_no_truncation():
        signals = [SimpleNamespace(signal_type="err", excerpt="short")]
        result = _build_context(signals)
        assert result == "[err] short"


class TestLooksTruncated:
    @staticmethod
    def test_balanced_not_truncated():
        assert _looks_truncated('[{"a": 1}]') is False

    @staticmethod
    def test_unbalanced_is_truncated():
        assert _looks_truncated('[{"a": 1}, {"b":') is True

    @staticmethod
    def test_slight_imbalance_not_truncated():
        assert _looks_truncated('[{"a": 1}') is False


class TestConversationSnippetTruncation:
    @staticmethod
    def test_long_content_gets_truncated():
        messages = [{"role": "user", "content": "x" * 1000}]
        snippet = _build_conversation_snippet(messages, content_preview_chars=50, language="en")
        assert "truncated" in snippet
        assert len(snippet) < 1000

    @staticmethod
    def test_recency_bias_last_messages_get_more_budget():
        messages = [{"role": "user", "content": "x" * 400} for _ in range(10)]
        snippet = _build_conversation_snippet(
            messages,
            content_preview_chars=200,
            language="cn",
        )
        lines = snippet.strip().split("\n")
        last_line = lines[-1]
        first_line = lines[0]
        assert len(last_line) > len(first_line)


class TestRetryParse:
    @staticmethod
    @pytest.mark.asyncio
    async def test_retry_on_malformed_json_sends_fix_prompt():
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content='[{"action":"append","target":"body","section":"Troubleshooting","content":"fixed"}]'
            )
        )
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        patches, retry_raw = await optimizer.retry_parse(
            broken_raw='[{"action":"append" invalid json}]',
            original_prompt="original prompt here",
        )
        assert len(patches) == 1
        assert patches[0].content == "fixed"
        assert retry_raw  # raw output returned for progressive retry
        call_args = llm.invoke.call_args
        prompt_sent = call_args.kwargs["messages"][0]["content"]
        assert "修复" in prompt_sent or "invalid json" in prompt_sent

    @staticmethod
    @pytest.mark.asyncio
    async def test_retry_on_truncated_uses_original_prompt():
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content='[{"action":"skip","skip_reason":"irrelevant"}]'))
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        truncated_raw = '[{"action":"append","target":"body","section":"Troubleshooting","content":"partial'
        patches, _ = await optimizer.retry_parse(
            broken_raw=truncated_raw,
            original_prompt="THE ORIGINAL PROMPT",
        )
        assert len(patches) == 1
        call_args = llm.invoke.call_args
        prompt_sent = call_args.kwargs["messages"][0]["content"]
        assert prompt_sent == "THE ORIGINAL PROMPT"

    @staticmethod
    @pytest.mark.asyncio
    async def test_retry_returns_empty_on_double_failure():
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="still broken"))
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        patches, retry_raw = await optimizer.retry_parse("bad", original_prompt="p")
        assert patches is None
        assert retry_raw == "still broken"  # raw returned for caller

    @staticmethod
    @pytest.mark.asyncio
    async def test_retry_returns_empty_on_llm_exception():
        llm = MagicMock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("network"))
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        patches, retry_raw = await optimizer.retry_parse("bad", original_prompt="p")
        assert patches is None
        assert retry_raw == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_retry_passes_parse_error_to_fix_prompt():
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content='[{"action":"skip","skip_reason":"irrelevant"}]'))
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        await optimizer.retry_parse(
            broken_raw="not json at all",
            original_prompt="orig",
            parse_error="Expecting value: line 1 column 1",
        )
        prompt_sent = llm.invoke.call_args.kwargs["messages"][0]["content"]
        assert "Expecting value: line 1 column 1" in prompt_sent

    @staticmethod
    @pytest.mark.asyncio
    async def test_retry_truncated_attempt_3_gives_up():
        llm = MagicMock()
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        truncated_raw = '[{"action":"append","target":"body"'
        patches, retry_raw = await optimizer.retry_parse(
            broken_raw=truncated_raw,
            original_prompt="orig",
            attempt_number=3,
        )
        assert patches is None
        assert retry_raw == truncated_raw
        assert llm.invoke.call_count == 0


class TestGenerateRecordsRetry:
    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_array_does_not_trigger_retry():
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="[]"))
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[make_signal()],
            skill_content="",
            messages=[],
            existing_desc_records=[],
            existing_body_records=[],
            user_query="",
        )
        records = await optimizer.generate_records(ctx)
        assert records == []
        assert llm.invoke.call_count == 1  # only initial call, no retry

    @staticmethod
    @pytest.mark.asyncio
    async def test_retry_passes_parse_error_in_prompt():
        call_count = 0

        async def fake_invoke(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return SimpleNamespace(content="not json at all")
            return SimpleNamespace(
                content='[{"action":"append","target":"body","section":"Troubleshooting","content":"recovered"}]'
            )

        llm = MagicMock()
        llm.invoke = fake_invoke
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[make_signal()],
            skill_content="",
            messages=[],
            existing_desc_records=[],
            existing_body_records=[],
            user_query="",
        )
        records = await optimizer.generate_records(ctx)
        assert call_count == 2  # initial + 1 retry
        assert len(records) == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_progressive_raw_update_on_double_failure():
        """When both retries fail, last_raw is updated from each retry_parse return."""
        call_count = 0
        prompts_sent = []

        async def fake_invoke(**kwargs):
            nonlocal call_count
            call_count += 1
            prompts_sent.append(kwargs["messages"][0]["content"])
            if call_count == 1:
                return SimpleNamespace(content="broken1")
            if call_count == 2:
                return SimpleNamespace(content="broken2")
            return SimpleNamespace(
                content='[{"action":"append","target":"body","section":"Examples","content":"final"}]'
            )

        llm = MagicMock()
        llm.invoke = fake_invoke
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="cn")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[make_signal()],
            skill_content="",
            messages=[],
            existing_desc_records=[],
            existing_body_records=[],
            user_query="",
        )
        records = await optimizer.generate_records(ctx)
        assert call_count == 3  # initial + 2 retries
        assert len(records) == 1
        assert records[0].change.content == "final"
        # Verify attempt 3 got the strict fix prompt (contains "严格要求")
        assert "严格要求" in prompts_sent[2]


class TestScriptLimit:
    @staticmethod
    @pytest.mark.asyncio
    async def test_text_and_script_limits_independent():
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content="""[
  {"action":"append","target":"body","section":"Troubleshooting","content":"A"},
  {"action":"append","target":"body","section":"Examples","content":"B"},
  {"action":"append","target":"body","section":"Instructions","content":"C-overflow"},
  {"action":"append","target":"script","section":"Scripts","content":"import os","script_filename":"s.py","script_language":"python","script_purpose":"test"},
  {"action":"append","target":"script","section":"Scripts","content":"import sys","script_filename":"s2.py","script_language":"python","script_purpose":"test2"}
]"""
            )
        )
        optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="en")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[make_signal()],
            skill_content="# skill",
            messages=[{"role": "user", "content": "hello"}],
            existing_desc_records=[],
            existing_body_records=[],
        )
        records = await optimizer.generate_records(ctx)
        text_recs = [r for r in records if r.change.target != EvolutionTarget.SCRIPT]
        script_recs = [r for r in records if r.change.target == EvolutionTarget.SCRIPT]
        assert len(text_recs) == 2
        assert len(script_recs) == 1
        assert text_recs[0].change.content == "A"
        assert text_recs[1].change.content == "B"


class TestPromptRoleConstraints:
    """SKILL_EXPERIENCE_GENERATE_PROMPT must contain role constraints and Collaboration section."""

    @staticmethod
    def test_role_constraint_in_cn_prompt():
        """中文 prompt 必须包含角色约束段落。"""
        prompt = SKILL_EXPERIENCE_GENERATE_PROMPT["cn"]
        assert "角色约束" in prompt
        assert "演进经验必须遵从 Agent 的角色能力和主要任务目标" in prompt

    @staticmethod
    def test_role_constraint_in_en_prompt():
        """英文 prompt 必须包含角色约束段落。"""
        prompt = SKILL_EXPERIENCE_GENERATE_PROMPT["en"]
        assert "Role Constraints" in prompt or "role constraints" in prompt.lower()

    @staticmethod
    def test_collaboration_section_in_cn_prompt():
        """中文 prompt 的 section 参考列表必须包含 Collaboration。"""
        prompt = SKILL_EXPERIENCE_GENERATE_PROMPT["cn"]
        assert "Collaboration" in prompt

    @staticmethod
    def test_collaboration_section_in_en_prompt():
        """英文 prompt 的 section 参考列表必须包含 Collaboration。"""
        prompt = SKILL_EXPERIENCE_GENERATE_PROMPT["en"]
        assert "Collaboration" in prompt
