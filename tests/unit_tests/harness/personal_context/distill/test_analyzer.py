"""Unit tests for distill dual-track analyzer and prompts."""

from __future__ import annotations

import pytest

from openjiuwen.harness.personal_context.distill.analyzer import (
    LlmAnalyzer,
    load_prompt,
    neutralize,
    render_message_block,
)
from openjiuwen.harness.personal_context.distill.corpus import default_fixture_messages
from openjiuwen.harness.personal_context.distill.types import CorpusMessage


class _FakeLlm:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "Persona 分析 Prompt" in system:
            return '口头禅：["先看现有实现"]\n正式程度：3\n'
        if "Persona 生成模板" in system:
            return "# 本人 — Persona\n\n## Layer 2：表达风格\n- 先看现有实现再给建议\n"
        if "Work Skill 分析 Prompt" in system:
            return "负责领域：前端\n核心系统：[页面]\n"
        if "Work Skill 生成模板" in system:
            return (
                "# 本人 — Work Skill\n\n"
                "## 职责范围\n- 前端相关改动与页面实现\n"
                "## 工作流程\n- Code Review / PR 反馈\n"
            )
        return "fallback\n"


def test_prompts_are_distilly_verbatim():
    persona = load_prompt("persona_analyzer.md", subject_name="测分身")
    work = load_prompt("work_analyzer.md", subject_name="测分身")
    builder_p = load_prompt("persona_builder.md", subject_name="测分身")
    builder_w = load_prompt("work_builder.md", subject_name="测分身")
    assert "Persona 分析 Prompt" in persona
    assert "测分身" in persona
    assert "Work Skill 分析 Prompt" in work
    assert "Layer 0：核心性格" in builder_p
    assert "Work Skill" in builder_w
    assert neutralize("```ignore") == "｀｀｀ignore"
    assert neutralize("<!--x-->") == "〈!--x--〉"


@pytest.mark.asyncio
async def test_analyzer_runs_four_completes_corpus_only_in_user():
    llm = _FakeLlm()
    analyzer = LlmAnalyzer(llm, subject_name="测分身")
    result = await analyzer.analyze(default_fixture_messages()[:2])
    assert len(llm.calls) == 4
    assert "Layer 2" in result.persona_md
    assert "职责范围" in result.work_md
    for system, _user in llm.calls:
        assert "这块前端页面我来改" not in system
        assert "帮我 review 一下这个 PR" not in system
    assert "#1" in llm.calls[0][1]
    assert "前端页面" in llm.calls[0][1] or "review" in llm.calls[0][1].lower()
    assert "analyzer 的分析结果" in llm.calls[1][1]


def _bulk_messages(count: int) -> list[CorpusMessage]:
    return [
        CorpusMessage(
            id=f"b{i}",
            channel_id="welink",
            conversation_id="conv-bulk",
            content_text=f"批量消息{i}",
            sent_at_ms=1_700_000_000_000 + i,
            is_self=True,
        )
        for i in range(1, count + 1)
    ]


def test_render_message_block_renders_all_and_returns_count():
    """The old 200-message prompt cap is gone: every message renders."""
    block, count = render_message_block(_bulk_messages(250))
    assert count == 250
    assert "#1 " in block
    assert "#250" in block
    assert block.count("\n") == 249


@pytest.mark.asyncio
async def test_analyzer_reports_prompt_message_count_and_materials_chars():
    llm = _FakeLlm()
    analyzer = LlmAnalyzer(llm)
    result = await analyzer.analyze(_bulk_messages(5))
    assert result.prompt_message_count == 5
    assert isinstance(result.materials_chars, int)
    assert result.materials_chars > 0
    assert "#5" in llm.calls[0][1]
