# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for online skill evolver."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.online.evolver import SkillEvolver, build_conversation_snippet
from openjiuwen.agent_evolving.online.schema import (
    EvolutionCategory,
    EvolutionContext,
    EvolutionPatch,
    EvolutionRecord,
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
        snippet = build_conversation_snippet(messages, language="cn")
        assert "[user] line1\nline2" in snippet
        assert "(tool_calls: read_file, bash)" in snippet
        assert "无文本" in snippet

    @staticmethod
    def test_build_conversation_snippet_limits_messages():
        messages = [{"role": "user", "content": f"m{i}"} for i in range(5)]
        snippet = build_conversation_snippet(messages, max_messages=2, language="en")
        assert "[user] m0" not in snippet
        assert "[user] m3" in snippet
        assert "[user] m4" in snippet


class TestSkillEvolverGenerate:
    @staticmethod
    @pytest.mark.asyncio
    async def test_generate_returns_empty_when_no_signals():
        llm = MagicMock()
        evolver = SkillEvolver(llm=llm, model="dummy", language="cn")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[],
            skill_content="# skill",
            messages=[],
            existing_desc_records=[],
            existing_body_records=[],
        )
        result = await evolver.generate_skill_experience(ctx)
        assert result == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_generate_returns_empty_on_llm_exception():
        llm = MagicMock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("network failed"))
        evolver = SkillEvolver(llm=llm, model="dummy", language="cn")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[make_signal()],
            skill_content="# skill",
            messages=[{"role": "user", "content": "hello"}],
            existing_desc_records=[],
            existing_body_records=[],
        )
        assert await evolver.generate_skill_experience(ctx) == []

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
        evolver = SkillEvolver(llm=llm, model="dummy", language="en")
        ctx = EvolutionContext(
            skill_name="skill-a",
            signals=[make_signal("s1"), make_signal("s2")],
            skill_content="# skill",
            messages=[{"role": "user", "content": "hello"}],
            existing_desc_records=[make_record("ev_d1", "desc old")],
            existing_body_records=[make_record("ev_b1", "body old")],
        )
        records = await evolver.generate_skill_experience(ctx)
        assert len(records) == 2
        assert records[0].change.content == "A"
        assert records[1].change.content == "B"

    @staticmethod
    def test_update_llm_updates_runtime_references():
        evolver = SkillEvolver(llm="old", model="m1", language="cn")
        evolver.update_llm(llm="new", model="m2")
        assert evolver._llm == "new"
        assert evolver._model == "m2"


class TestSkillEvolverParsing:
    @staticmethod
    def test_parse_llm_response_supports_json_codeblock_and_fallback():
        codeblock = """```json
[
  {"action":"append","target":"body","section":"Troubleshooting","content":"A","merge_target":"null"}
]
```"""
        patches = SkillEvolver._parse_llm_response(codeblock)
        assert len(patches) == 1
        assert patches[0].merge_target is None

        mixed = (
            "prefix text "
            "{\"action\":\"append\",\"target\":\"invalid\","
            "\"section\":\"NotExist\",\"content\":\"X\"} suffix"
        )
        patches2 = SkillEvolver._parse_llm_response(mixed)
        assert len(patches2) == 1
        assert patches2[0].section == "Troubleshooting"
        assert patches2[0].target == EvolutionTarget.BODY

    @staticmethod
    def test_parse_llm_response_invalid_returns_empty():
        assert SkillEvolver._parse_llm_response("not json at all") == []

    @staticmethod
    def test_parse_single_patch_skip():
        patch = SkillEvolver._parse_single_patch({"action": "skip", "skip_reason": "irrelevant"})
        assert patch.action == "skip"
        assert patch.skip_reason == "irrelevant"
