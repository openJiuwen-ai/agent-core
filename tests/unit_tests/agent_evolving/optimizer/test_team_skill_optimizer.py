# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for TeamSkillOptimizer prompt templates and patch generation methods."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.optimizer.team_skill_optimizer import (
    TeamSkillOptimizer,
    _TRAJECTORY_PATCH_PROMPT,
    _USER_PATCH_PROMPT,
)


def test_user_patch_prompt_cn_exists():
    """主动 patch prompt 中文必须存在。"""
    assert "cn" in _USER_PATCH_PROMPT
    assert "用户意见" in _USER_PATCH_PROMPT["cn"]


def test_user_patch_prompt_en_exists():
    """主动 patch prompt 英文必须存在。"""
    assert "en" in _USER_PATCH_PROMPT


def test_trajectory_patch_prompt_cn_exists():
    """被动 patch prompt 中文必须存在。"""
    assert "cn" in _TRAJECTORY_PATCH_PROMPT
    assert "轨迹分析" in _TRAJECTORY_PATCH_PROMPT["cn"] or "执行轨迹" in _TRAJECTORY_PATCH_PROMPT["cn"]


def test_trajectory_patch_prompt_en_exists():
    """被动 patch prompt 英文必须存在。"""
    assert "en" in _TRAJECTORY_PATCH_PROMPT


# ---------------------------------------------------------------------------
# generate_user_patch
# ---------------------------------------------------------------------------

class _MockTrajectory:
    """Minimal trajectory-like object for tests."""

    def __init__(self, steps: list[dict] | None = None) -> None:
        self.steps = steps or []


def _make_optimizer(llm_mock: Any, language: str = "cn") -> TeamSkillOptimizer:
    llm_mock.invoke = AsyncMock()
    return TeamSkillOptimizer(llm=llm_mock, model="test-model", language=language)


class TestGenerateUserPatch:
    """端到端测试 generate_user_patch 方法。"""

    @pytest.mark.asyncio
    async def test_returns_record_on_valid_response(self):
        """LLM 返回有效 JSON 时应生成 EvolutionRecord。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm, language="cn")

        patch_json = json.dumps({
            "section": "Collaboration",
            "action": "append",
            "content": "角色 A 完成后通知角色 B",
        })
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=patch_json))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "改进协作流程")

        assert record is not None
        assert record.change.section == "Collaboration"
        assert "角色 A 完成后通知角色 B" in record.change.content
        assert record.source == "team_skill_user_patch"

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self):
        """LLM 返回空值时应返回 None。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=""))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "test intent")

        assert record is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self):
        """LLM 返回非 JSON 内容时应返回 None。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="not json at all"))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "test intent")

        assert record is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_content(self):
        """LLM 返回 content 为空的 patch 时应返回 None。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(
            content=json.dumps({"section": "Instructions", "action": "append", "content": ""})
        ))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "test intent")

        assert record is None


# ---------------------------------------------------------------------------
# generate_trajectory_patch
# ---------------------------------------------------------------------------

class TestGenerateTrajectoryPatch:
    """端到端测试 generate_trajectory_patch 方法。"""

    @pytest.mark.asyncio
    async def test_returns_record_when_need_patch_true(self):
        """LLM 返回 need_patch=true 时应生成 EvolutionRecord。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)

        response = json.dumps({
            "need_patch": True,
            "section": "Constraints",
            "content": "执行超时不得超过 30 秒",
            "reason": "轨迹显示多次超时",
        })
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=response))

        trajectory = _MockTrajectory()
        issues = [{"issue_type": "timeout", "description": "多次超时", "severity": "high"}]
        record = await optimizer.generate_trajectory_patch(trajectory, "test-skill", issues)

        assert record is not None
        assert record.change.section == "Constraints"
        assert "执行超时不得超过 30 秒" in record.change.content
        assert record.source == "team_skill_trajectory_patch"

    @pytest.mark.asyncio
    async def test_returns_none_when_need_patch_false(self):
        """LLM 返回 need_patch=false 时应返回 None。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(
            content=json.dumps({"need_patch": False, "reason": "轨迹无异常"})
        ))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_trajectory_patch(trajectory, "test-skill", [])

        assert record is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self):
        """LLM 返回空值时应返回 None。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=""))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_trajectory_patch(trajectory, "test-skill", [])

        assert record is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self):
        """LLM 返回非 JSON 内容时应返回 None。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="broken json"))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_trajectory_patch(trajectory, "test-skill", [])

        assert record is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_content(self):
        """LLM 返回 content 为空时应返回 None。"""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(
            content=json.dumps({"need_patch": True, "section": "Workflow", "content": "   "})
        ))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_trajectory_patch(trajectory, "test-skill", [])

        assert record is None
