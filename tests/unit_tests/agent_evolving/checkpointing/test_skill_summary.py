# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for skill-level experiences summary generation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.checkpointing.skill_summary import (
    fallback_skill_experiences_summary,
    generate_skill_experiences_summary,
)
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
)
from openjiuwen.agent_evolving.signal.base import EvolutionTarget


def _make_record(record_id: str, summary: str, root_cause: str = "") -> EvolutionRecord:
    return EvolutionRecord(
        id=record_id,
        source="execution_failure",
        timestamp="2026-01-01T00:00:00+00:00",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=f"## {summary}\n- detail",
            target=EvolutionTarget.BODY,
            summary=summary,
        ),
        summary=summary,
        root_cause=root_cause or None,
    )


class TestSkillExperiencesSummary:
    @staticmethod
    def test_fallback_joins_and_caps():
        summary = fallback_skill_experiences_summary(
            "skill-a",
            [
                _make_record("ev_1", "超时先重试"),
                _make_record("ev_2", "字" * 90),
            ],
        )
        assert summary is not None
        assert "超时先重试" in summary
        assert len(summary) <= 100

    @staticmethod
    @pytest.mark.asyncio
    async def test_generate_uses_llm_and_caps():
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(content="覆盖超时重试与权限排查的核心指引。" + "x" * 80)
        )
        summary = await generate_skill_experiences_summary(
            "skill-a",
            [
                _make_record("ev_1", "超时先重试", "缺超时指引"),
                _make_record("ev_2", "权限用sudo", "缺权限说明"),
            ],
            llm=llm,
            model="dummy",
            language="cn",
        )
        assert summary is not None
        assert len(summary) <= 100
        assert "超时" in summary or "权限" in summary or "指引" in summary
        llm.invoke.assert_awaited_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_generate_falls_back_when_llm_fails():
        llm = MagicMock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("boom"))
        summary = await generate_skill_experiences_summary(
            "skill-a",
            [_make_record("ev_1", "超时先重试")],
            llm=llm,
            model="dummy",
            language="cn",
        )
        assert summary == "超时先重试"
