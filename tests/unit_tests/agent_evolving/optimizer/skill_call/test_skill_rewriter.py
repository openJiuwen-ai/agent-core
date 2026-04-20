# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for SkillRewriter."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionLog,
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.optimizer.skill_call.skill_rewriter import (
    SkillRewriter,
    SkillRewriteResult,
    SKILL_REWRITE_PROMPT,
    _RETRY_PROMPT,
)


def make_record(
    record_id: str,
    content: str = "test content",
    score: float = 0.7,
    target: EvolutionTarget = EvolutionTarget.BODY,
    section: str = "Troubleshooting",
    skip_reason: str | None = None,
) -> EvolutionRecord:
    """Create a test evolution record."""
    return EvolutionRecord(
        id=record_id,
        source="execution_failure",
        timestamp="2026-01-01T00:00:00+00:00",
        context="test context",
        change=EvolutionPatch(
            section=section,
            action="append",
            content=content,
            target=target,
            skip_reason=skip_reason,
        ),
        score=score,
        applied=False,
    )


def make_store_mock(
    skill_content: str = "",
    entries: list | None = None,
) -> MagicMock:
    """Create a mock EvolutionStore."""
    store = MagicMock()
    store.read_skill_content = AsyncMock(return_value=skill_content)
    store.load_evolution_log = AsyncMock(
        return_value=EvolutionLog(
            skill_id="test-skill",
            entries=entries or [],
        )
    )
    store.write_skill_content = AsyncMock(return_value=True)
    store.delete_records = AsyncMock(return_value=0)
    return store


class TestSkillRewriterInit:
    """Test SkillRewriter initialization and basic properties."""

    @staticmethod
    def test_init_with_defaults():
        llm = MagicMock()
        rewriter = SkillRewriter(llm=llm, model="gpt-4")
        assert rewriter._llm == llm
        assert rewriter._model == "gpt-4"
        assert rewriter._language == "cn"

    @staticmethod
    def test_init_with_language():
        llm = MagicMock()
        rewriter = SkillRewriter(llm=llm, model="gpt-4", language="en")
        assert rewriter._language == "en"

    @staticmethod
    def test_update_llm():
        rewriter = SkillRewriter(llm="old", model="m1")
        rewriter.update_llm(llm="new", model="m2")
        assert rewriter._llm == "new"
        assert rewriter._model == "m2"


class TestRewriteReturnsNone:
    """Test rewrite() returns None in edge cases."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_returns_none_when_no_skill_content():
        store = make_store_mock(skill_content="")
        llm = MagicMock()
        rewriter = SkillRewriter(llm=llm, model="dummy")

        result = await rewriter.rewrite("test-skill", store)

        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_returns_none_when_no_evolution_records():
        store = make_store_mock(
            skill_content="# Test Skill",
            entries=[],
        )
        llm = MagicMock()
        rewriter = SkillRewriter(llm=llm, model="dummy")

        result = await rewriter.rewrite("test-skill", store)

        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_returns_none_when_all_records_below_min_score():
        store = make_store_mock(
            skill_content="# Test Skill",
            entries=[make_record("ev_001", score=0.3)],
        )
        llm = MagicMock()
        rewriter = SkillRewriter(llm=llm, model="dummy")

        result = await rewriter.rewrite("test-skill", store, min_score=0.5)

        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_returns_none_when_all_records_skipped():
        store = make_store_mock(
            skill_content="# Test Skill",
            entries=[make_record("ev_001", skip_reason="irrelevant")],
        )
        llm = MagicMock()
        rewriter = SkillRewriter(llm=llm, model="dummy")

        result = await rewriter.rewrite("test-skill", store)

        assert result is None


class TestRewriteSuccess:
    """Test successful rewrite scenarios."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_success_returns_result():
        original_content = """---
name: test-skill
description: A test skill
---

# Test Skill

Some instructions here.
"""
        rewritten_content = """---
name: test-skill
description: A test skill
---

# Test Skill

Updated instructions with integrated experience.
"""
        store = make_store_mock(
            skill_content=original_content,
            entries=[make_record("ev_001", content="New guidance")],
        )
        store.delete_records = AsyncMock(return_value=1)

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=f"```markdown\n{rewritten_content}\n```"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is not None
        assert isinstance(result, SkillRewriteResult)
        assert result.skill_name == "test-skill"
        assert result.original_content == original_content
        # Content is stripped during extraction, so compare stripped versions
        assert result.rewritten_content == rewritten_content.strip()
        assert "ev_001" in result.consumed_record_ids
        assert result.records_cleaned == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_dry_run_no_side_effects():
        original_content = "# Test Skill\n\nOriginal content."
        rewritten_content = "# Test Skill\n\nUpdated content."
        store = make_store_mock(
            skill_content=original_content,
            entries=[make_record("ev_001")],
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=f"```markdown\n{rewritten_content}\n```"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store, dry_run=True)

        assert result is not None
        assert result.records_cleaned == 0
        store.write_skill_content.assert_not_called()
        store.delete_records.assert_not_called()


class TestRewriteFiltersByMinScore:
    """Test min_score filtering behavior."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_filters_by_min_score():
        store = make_store_mock(
            skill_content="# Test Skill",
            entries=[
                make_record("ev_high", score=0.8),
                make_record("ev_low", score=0.3),
            ],
        )
        store.delete_records = AsyncMock(return_value=1)

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="```markdown\n# Test Skill\n\nUpdated.\n```"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store, min_score=0.5)

        assert result is not None
        assert len(result.consumed_record_ids) == 1
        assert "ev_high" in result.consumed_record_ids
        assert "ev_low" not in result.consumed_record_ids


class TestRewritePreservesFrontMatter:
    """Test that YAML front-matter is preserved in output."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_preserves_front_matter():
        original = """---
name: my-skill
description: My description
version: 1.0.0
---

# My Skill

Content here.
"""
        rewritten = """---
name: my-skill
description: My description
version: 1.0.0
---

# My Skill

Updated content.
"""
        store = make_store_mock(
            skill_content=original,
            entries=[make_record("ev_001")],
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=f"```markdown\n{rewritten}\n```"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is not None
        assert result.rewritten_content.startswith("---")

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_fails_validation_when_front_matter_missing():
        original = """---
name: my-skill
---

# My Skill
"""
        rewritten = "# My Skill\n\nNo front matter here."
        store = make_store_mock(
            skill_content=original,
            entries=[make_record("ev_001")],
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=rewritten))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is None


class TestRewriteHandlesLlmErrors:
    """Test error handling for LLM failures."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_returns_none_on_llm_exception():
        store = make_store_mock(
            skill_content="# Test Skill",
            entries=[make_record("ev_001")],
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("network error"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_returns_none_on_unparseable_output():
        store = make_store_mock(
            skill_content="# Test Skill",
            entries=[make_record("ev_001")],
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="not valid markdown output"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is None


class TestRewriteRetry:
    """Test retry logic for malformed output."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_retry_on_malformed_output():
        store = make_store_mock(
            skill_content="# Test Skill",
            entries=[make_record("ev_001")],
        )
        store.delete_records = AsyncMock(return_value=1)

        llm = MagicMock()
        # First call returns garbage, second returns valid
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(content="not markdown"),
                SimpleNamespace(content="```markdown\n# Test Skill\n\nFixed.\n```"),
            ]
        )

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is not None
        assert llm.invoke.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_returns_none_when_retry_also_fails():
        store = make_store_mock(
            skill_content="# Test Skill",
            entries=[make_record("ev_001")],
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="still not valid"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is None
        assert llm.invoke.call_count == 2  # Original + 1 retry


class TestRewriteValidation:
    """Test output validation logic."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_fails_when_content_too_short():
        original = "# Test Skill\n\n" + "x" * 1000
        rewritten = "# Test"  # Too short
        store = make_store_mock(
            skill_content=original,
            entries=[make_record("ev_001")],
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=f"```markdown\n{rewritten}\n```"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_rewrite_fails_when_no_headings():
        original = "# Test Skill\n\nContent."
        rewritten = "Just plain text without any headings."
        store = make_store_mock(
            skill_content=original,
            entries=[make_record("ev_001")],
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=f"```markdown\n{rewritten}\n```"))

        rewriter = SkillRewriter(llm=llm, model="dummy")
        result = await rewriter.rewrite("test-skill", store)

        assert result is None


class TestFormatExperiences:
    """Test _format_experiences_by_section method."""

    @staticmethod
    def test_format_experiences_by_section():
        records = [
            make_record(
                "ev_001", content="Body content A", score=0.8, target=EvolutionTarget.BODY, section="Troubleshooting"
            ),
            make_record(
                "ev_002", content="Body content B", score=0.6, target=EvolutionTarget.BODY, section="Troubleshooting"
            ),
            make_record(
                "ev_003", content="Desc content", score=0.9, target=EvolutionTarget.DESCRIPTION, section="Instructions"
            ),
        ]

        rewriter = SkillRewriter(llm=MagicMock(), model="dummy")
        result = rewriter._format_experiences_by_section(records)

        assert "body / Troubleshooting" in result
        assert "description / Instructions" in result
        assert "ev_001" in result
        assert "ev_002" in result
        assert "ev_003" in result
        # Higher score should appear first
        assert result.index("ev_001") < result.index("ev_002")

    @staticmethod
    def test_format_experiences_empty():
        rewriter = SkillRewriter(llm=MagicMock(), model="dummy")
        result = rewriter._format_experiences_by_section([])

        assert "无有效经验记录" in result or "No valid experience records" in result


class TestExtractMarkdown:
    """Test _extract_markdown method."""

    @staticmethod
    def test_extract_from_markdown_code_block():
        rewriter = SkillRewriter(llm=MagicMock(), model="dummy")
        raw = "```markdown\n# Content\n\nText.\n```"

        result = rewriter._extract_markdown(raw)

        assert result == "# Content\n\nText."

    @staticmethod
    def test_extract_from_generic_code_block():
        rewriter = SkillRewriter(llm=MagicMock(), model="dummy")
        raw = "```\n# Content\n\nText.\n```"

        result = rewriter._extract_markdown(raw)

        assert result == "# Content\n\nText."

    @staticmethod
    def test_extract_raw_when_starts_with_front_matter():
        rewriter = SkillRewriter(llm=MagicMock(), model="dummy")
        raw = "---\nname: test\n---\n\n# Content"

        result = rewriter._extract_markdown(raw)

        assert result == raw

    @staticmethod
    def test_extract_returns_none_for_invalid():
        rewriter = SkillRewriter(llm=MagicMock(), model="dummy")
        raw = "just plain text without markdown"

        result = rewriter._extract_markdown(raw)

        assert result is None


class TestGenerateSummary:
    """Test _generate_summary method."""

    @staticmethod
    def test_generate_summary_cn():
        records = [
            make_record("ev_001", target=EvolutionTarget.BODY, section="Troubleshooting"),
            make_record("ev_002", target=EvolutionTarget.DESCRIPTION, section="Instructions"),
        ]
        original = "Line 1\nLine 2"
        rewritten = "Line 1\nLine 2\nLine 3"

        rewriter = SkillRewriter(llm=MagicMock(), model="dummy", language="cn")
        summary = rewriter._generate_summary(records, original, rewritten)

        assert "2 条" in summary
        assert "body" in summary
        assert "description" in summary
        assert "2 -> 3" in summary

    @staticmethod
    def test_generate_summary_en():
        records = [
            make_record("ev_001", target=EvolutionTarget.BODY, section="Troubleshooting"),
        ]
        original = "Line 1"
        rewritten = "Line 1\nLine 2"

        rewriter = SkillRewriter(llm=MagicMock(), model="dummy", language="en")
        summary = rewriter._generate_summary(records, original, rewritten)

        assert "1 experience" in summary
        assert "body" in summary
        assert "1 -> 2" in summary


class TestPromptsExist:
    """Verify prompts are defined for both languages."""

    @staticmethod
    def test_prompts_defined_for_cn_and_en():
        assert "cn" in SKILL_REWRITE_PROMPT
        assert "en" in SKILL_REWRITE_PROMPT
        assert "{skill_content}" in SKILL_REWRITE_PROMPT["cn"]
        assert "{experiences_by_section}" in SKILL_REWRITE_PROMPT["cn"]
        assert "{user_query}" in SKILL_REWRITE_PROMPT["cn"]
        assert "{user_query}" in SKILL_REWRITE_PROMPT["en"]

    @staticmethod
    def test_retry_prompts_defined():
        assert "cn" in _RETRY_PROMPT
        assert "en" in _RETRY_PROMPT
        assert "{broken_preview}" in _RETRY_PROMPT["cn"]
