# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for TeamSkillOptimizer prompt templates and patch generation methods."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.optimizer.team_skill_optimizer import (
    TeamSkillOptimizer,
    _TRAJECTORY_PATCH_PROMPT,
    _USER_PATCH_PROMPT,
)
from openjiuwen.core.common.exception.errors import BaseError


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


def _tool_step(
    tool_name: str = "spawn_member",
    *,
    args_text: str = "arg " * 1000,
    result_text: str = "result " * 1000,
) -> SimpleNamespace:
    return SimpleNamespace(
        kind="tool",
        detail=SimpleNamespace(
            tool_name=tool_name,
            call_args=args_text,
            call_result=result_text,
        ),
    )


def _make_optimizer(llm_mock: Any, language: str = "cn") -> TeamSkillOptimizer:
    llm_mock.invoke = AsyncMock()
    return TeamSkillOptimizer(llm=llm_mock, model="test-model", language=language)


def test_patch_llm_policy_property_returns_configured_policy():
    """The public property should expose the effective patch policy."""
    policy = LLMInvokePolicy(attempt_timeout_secs=15, total_budget_secs=45, max_attempts=2)
    optimizer = TeamSkillOptimizer(
        llm=MagicMock(),
        model="test-model",
        language="en",
        patch_llm_policy=policy,
    )

    assert optimizer.patch_llm_policy is policy


class TestGenerateUserPatch:
    """End-to-end tests for generate_user_patch."""

    @pytest.mark.asyncio
    async def test_returns_record_on_valid_response(self):
        """A valid JSON response should produce an EvolutionRecord."""
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
        assert llm.invoke.await_args_list[0].kwargs["timeout"] == 120

    @pytest.mark.asyncio
    async def test_raises_on_empty_response(self):
        """An empty response should not silently degrade."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=""))

        trajectory = _MockTrajectory()
        with pytest.raises(BaseError):
            await optimizer.generate_user_patch(trajectory, "test-skill", "test intent")

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self):
        """A non-JSON response should not silently degrade."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="not json at all"))

        trajectory = _MockTrajectory()
        with pytest.raises(BaseError):
            await optimizer.generate_user_patch(trajectory, "test-skill", "test intent")

    @pytest.mark.asyncio
    async def test_raises_on_empty_content(self):
        """A patch with empty content should not silently degrade."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(
            content=json.dumps({"section": "Instructions", "action": "append", "content": ""})
        ))

        trajectory = _MockTrajectory()
        with pytest.raises(ValueError, match="empty content"):
            await optimizer.generate_user_patch(trajectory, "test-skill", "test intent")

    @pytest.mark.asyncio
    async def test_retries_when_first_response_is_invalid_json(self):
        """The optimizer should retry after an invalid first response."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(content="not json"),
                SimpleNamespace(content=json.dumps({
                    "section": "Collaboration",
                    "action": "append",
                    "content": "先同步上下文，再分派角色",
                })),
            ]
        )

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "优化协作")

        assert record is not None
        assert record.change.section == "Collaboration"
        assert llm.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_with_shorter_prompt_after_timeout(self):
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(
            side_effect=[
                asyncio.TimeoutError("request timed out"),
                SimpleNamespace(content=json.dumps({
                    "section": "Collaboration",
                    "action": "append",
                    "content": "先同步上下文，再分派角色",
                })),
            ]
        )

        trajectory = _MockTrajectory(steps=[_tool_step()])
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "优化协作 " * 400)

        assert record is not None
        first_prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
        second_prompt = llm.invoke.await_args_list[1].kwargs["messages"][0]["content"]
        assert len(second_prompt) < len(first_prompt)


# ---------------------------------------------------------------------------
# generate_trajectory_patch
# ---------------------------------------------------------------------------

class TestGenerateTrajectoryPatch:
    """End-to-end tests for generate_trajectory_patch."""

    _CURRENT_SKILL_CONTENT = "# Team Skill\n## Workflow\nKeep the reviewer handoff explicit."

    @pytest.mark.asyncio
    async def test_returns_record_when_need_patch_true(self):
        """need_patch=true should produce an EvolutionRecord."""
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
        record = await optimizer.generate_trajectory_patch(
            trajectory,
            "test-skill",
            self._CURRENT_SKILL_CONTENT,
            issues,
        )

        assert record is not None
        assert record.change.section == "Constraints"
        assert "执行超时不得超过 30 秒" in record.change.content
        assert record.source == "team_skill_trajectory_patch"
        assert llm.invoke.await_args_list[0].kwargs["timeout"] == 120
        prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
        assert self._CURRENT_SKILL_CONTENT in prompt

    @pytest.mark.asyncio
    async def test_uses_custom_patch_llm_policy(self):
        """A custom patch policy should override the default timeout."""
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps({
                    "need_patch": True,
                    "section": "Workflow",
                    "content": "Coordinate the reviewer handoff.",
                    "reason": "Observed repeated context loss.",
                })
            )
        )
        optimizer = TeamSkillOptimizer(
            llm=llm,
            model="test-model",
            language="en",
            patch_llm_policy=LLMInvokePolicy(
                attempt_timeout_secs=15,
                total_budget_secs=45,
                max_attempts=2,
            ),
        )

        trajectory = _MockTrajectory()
        record = await optimizer.generate_trajectory_patch(
            trajectory,
            "test-skill",
            self._CURRENT_SKILL_CONTENT,
            [{"issue_type": "handoff"}],
        )

        assert record is not None
        assert llm.invoke.await_args_list[0].kwargs["timeout"] == 15

    @pytest.mark.asyncio
    async def test_returns_none_when_need_patch_false(self):
        """need_patch=false should return None."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(
            content=json.dumps({"need_patch": False, "reason": "轨迹无异常"})
        ))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_trajectory_patch(
            trajectory,
            "test-skill",
            self._CURRENT_SKILL_CONTENT,
            [],
        )

        assert record is None

    @pytest.mark.asyncio
    async def test_raises_on_empty_response(self):
        """An empty response should not silently degrade."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=""))

        trajectory = _MockTrajectory()
        with pytest.raises(BaseError):
            await optimizer.generate_trajectory_patch(
                trajectory,
                "test-skill",
                self._CURRENT_SKILL_CONTENT,
                [],
            )

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self):
        """A non-JSON response should not silently degrade."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="broken json"))

        trajectory = _MockTrajectory()
        with pytest.raises(BaseError):
            await optimizer.generate_trajectory_patch(
                trajectory,
                "test-skill",
                self._CURRENT_SKILL_CONTENT,
                [],
            )

    @pytest.mark.asyncio
    async def test_raises_on_empty_content(self):
        """A response with empty content should not silently degrade."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(return_value=SimpleNamespace(
            content=json.dumps({"need_patch": True, "section": "Workflow", "content": "   "})
        ))

        trajectory = _MockTrajectory()
        with pytest.raises(ValueError, match="empty content"):
            await optimizer.generate_trajectory_patch(
                trajectory,
                "test-skill",
                self._CURRENT_SKILL_CONTENT,
                [],
            )

    @pytest.mark.asyncio
    async def test_retries_with_shorter_prompt_after_timeout(self):
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(
            side_effect=[
                asyncio.TimeoutError("request timed out"),
                SimpleNamespace(content=json.dumps({
                    "need_patch": True,
                    "section": "Workflow",
                    "content": "失败后先收敛上下文，再重试",
                    "reason": "轨迹过长",
                })),
            ]
        )

        trajectory = _MockTrajectory(steps=[_tool_step(), _tool_step(tool_name="send_message")])
        issues = [{"issue_type": "timeout", "description": "超时 " * 1000, "severity": "high"}]
        record = await optimizer.generate_trajectory_patch(
            trajectory,
            "test-skill",
            self._CURRENT_SKILL_CONTENT * 500,
            issues,
        )

        assert record is not None
        first_prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
        second_prompt = llm.invoke.await_args_list[1].kwargs["messages"][0]["content"]
        assert len(second_prompt) < len(first_prompt)


class TestGeneratePatch:
    @pytest.mark.asyncio
    async def test_retries_with_shorter_prompt_after_timeout(self):
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(
            side_effect=[
                asyncio.TimeoutError("request timed out"),
                SimpleNamespace(content=json.dumps({
                    "need_patch": True,
                    "section": "Constraints",
                    "content": "限制上下文摘要长度",
                    "reason": "提示过长",
                })),
            ]
        )

        trajectory = _MockTrajectory(steps=[_tool_step(), _tool_step(tool_name="build_team")])
        record = await optimizer.generate_patch(trajectory, "test-skill", "# Skill\n" + ("content\n" * 4000))

        assert record is not None
        first_prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
        second_prompt = llm.invoke.await_args_list[1].kwargs["messages"][0]["content"]
        assert len(second_prompt) < len(first_prompt)
