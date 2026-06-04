# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for TeamSkillOptimizer prompt templates and patch generation methods."""

from __future__ import annotations

import asyncio
import json
from importlib import import_module
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.experience.types import EvolutionContext, OnlineEvolutionContext
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.optimizer.skill_call.team_skill_experience_optimizer import TeamSkillExperienceOptimizer
from openjiuwen.agent_evolving.signal.base import EvolutionTarget, make_evolution_signal
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError

_TEAM_MODULE = import_module("openjiuwen.agent_evolving.optimizer.skill_call.team_skill_experience_optimizer")
TeamSkillOptimizer = _TEAM_MODULE.TeamSkillOptimizer
TEAM_EXPERIENCE_GENERATE_PROMPT = _TEAM_MODULE.TEAM_EXPERIENCE_GENERATE_PROMPT
TRAJECTORY_PATCH_PROMPT = _TEAM_MODULE.TRAJECTORY_PATCH_PROMPT
USER_PATCH_PROMPT = _TEAM_MODULE.USER_PATCH_PROMPT


def test_team_skill_optimizer_compat_alias_points_to_formal_class():
    """Legacy TeamSkillOptimizer import should stay compatible."""
    assert TeamSkillOptimizer is TeamSkillExperienceOptimizer


def test_user_patch_prompt_cn_exists():
    """主动 patch prompt 中文必须存在。"""
    assert "cn" in USER_PATCH_PROMPT
    assert "用户意见" in USER_PATCH_PROMPT["cn"]
    assert "已有演进经验摘要" in USER_PATCH_PROMPT["cn"]
    assert "相关性判断" in USER_PATCH_PROMPT["cn"]
    assert "缺少交接" in USER_PATCH_PROMPT["cn"]
    assert "section 选择参考" in USER_PATCH_PROMPT["cn"]
    assert "只生成一条 patch" in USER_PATCH_PROMPT["cn"]
    assert "避免“加强协作”“优化流程”这类空话" in USER_PATCH_PROMPT["cn"]
    assert "need_patch" in USER_PATCH_PROMPT["cn"]
    assert '"summary"' in USER_PATCH_PROMPT["cn"]
    assert "duplicate | irrelevant | low_value" in USER_PATCH_PROMPT["cn"]


def test_user_patch_prompt_en_exists():
    """主动 patch prompt 英文必须存在。"""
    assert "en" in USER_PATCH_PROMPT
    assert "Existing evolution summary" in USER_PATCH_PROMPT["en"]
    assert "Relevance" in USER_PATCH_PROMPT["en"]
    assert "missing handoffs" in USER_PATCH_PROMPT["en"]
    assert "Section mapping guide" in USER_PATCH_PROMPT["en"]
    assert "Generate exactly one patch" in USER_PATCH_PROMPT["en"]
    assert 'avoid vague statements like "improve collaboration"' in USER_PATCH_PROMPT["en"]
    assert "need_patch=false" in USER_PATCH_PROMPT["en"]
    assert '"summary"' in USER_PATCH_PROMPT["en"]


def test_trajectory_patch_prompt_cn_exists():
    """被动 patch prompt 中文必须存在。"""
    assert "cn" in TRAJECTORY_PATCH_PROMPT
    assert "轨迹分析" in TRAJECTORY_PATCH_PROMPT["cn"] or "执行轨迹" in TRAJECTORY_PATCH_PROMPT["cn"]
    assert "已有演进经验摘要" in TRAJECTORY_PATCH_PROMPT["cn"]
    assert "相关性" in TRAJECTORY_PATCH_PROMPT["cn"]
    assert "去重性" in TRAJECTORY_PATCH_PROMPT["cn"]
    assert "优先级" in TRAJECTORY_PATCH_PROMPT["cn"]
    assert "section 选择参考" in TRAJECTORY_PATCH_PROMPT["cn"]
    assert "只输出一条 patch" in TRAJECTORY_PATCH_PROMPT["cn"]
    assert '"summary"' in TRAJECTORY_PATCH_PROMPT["cn"]
    assert "失败恢复" in TRAJECTORY_PATCH_PROMPT["cn"]


def test_trajectory_patch_prompt_en_exists():
    """被动 patch prompt 英文必须存在。"""
    assert "en" in TRAJECTORY_PATCH_PROMPT
    assert "Existing evolution summary" in TRAJECTORY_PATCH_PROMPT["en"]
    assert "Relevance" in TRAJECTORY_PATCH_PROMPT["en"]
    assert "Deduplication" in TRAJECTORY_PATCH_PROMPT["en"]
    assert "Priority" in TRAJECTORY_PATCH_PROMPT["en"]
    assert "Section mapping guide" in TRAJECTORY_PATCH_PROMPT["en"]
    assert "Output exactly one patch" in TRAJECTORY_PATCH_PROMPT["en"]
    assert '"summary"' in TRAJECTORY_PATCH_PROMPT["en"]
    assert "Missing recovery paths" in TRAJECTORY_PATCH_PROMPT["en"]


def test_team_aggregated_prompts_request_summary_field():
    for prompt in TEAM_EXPERIENCE_GENERATE_PROMPT.values():
        assert '"summary"' in prompt
        assert "summary" in prompt.lower()


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
    if not hasattr(llm_mock, "invoke"):
        llm_mock.invoke = AsyncMock()
    return TeamSkillOptimizer(llm=llm_mock, model="test-model", language=language)


def test_record_llm_policy_property_returns_configured_policy():
    """The public property should expose the effective record policy."""
    policy = LLMInvokePolicy(attempt_timeout_secs=15, total_budget_secs=45, max_attempts=2)
    optimizer = TeamSkillOptimizer(
        llm=MagicMock(),
        model="test-model",
        language="en",
        record_llm_policy=policy,
    )

    assert optimizer.record_llm_policy is policy


class TestGenerateUserPatch:
    """End-to-end tests for generate_user_patch."""

    @pytest.mark.asyncio
    async def test_returns_record_on_valid_response(self):
        """A valid JSON response should produce an EvolutionRecord."""
        llm = MagicMock()
        optimizer = _make_optimizer(llm, language="cn")

        patch_json = json.dumps(
            {
                "section": "Collaboration",
                "action": "append",
                "summary": "Require role A to notify role B before handoff.",
                "content": "角色 A 完成后通知角色 B",
            }
        )
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=patch_json))

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "改进协作流程")

        assert record is not None
        assert record.change.section == "Collaboration"
        assert record.summary == "Require role A to notify role B before handoff."
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
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps({"section": "Instructions", "action": "append", "content": ""})
            )
        )

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
                SimpleNamespace(
                    content=json.dumps(
                        {
                            "section": "Collaboration",
                            "action": "append",
                            "content": "先同步上下文，再分派角色",
                        }
                    )
                ),
            ]
        )

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "优化协作")

        assert record is not None
        assert record.change.section == "Collaboration"
        assert llm.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_when_first_response_is_json_array_not_patch_object(self):
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(content='[{"section":"Collaboration","action":"append","content":"not an object"}]'),
                SimpleNamespace(
                    content=json.dumps(
                        {
                            "section": "Collaboration",
                            "action": "append",
                            "content": "先同步上下文，再分派角色",
                        }
                    )
                ),
            ]
        )

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "优化协作")

        assert record is not None
        assert record.change.section == "Collaboration"
        assert llm.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_need_patch_false(self):
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps(
                    {
                        "need_patch": False,
                        "section": "",
                        "action": "skip",
                        "content": "",
                        "reason": "duplicate",
                    }
                )
            )
        )

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "重复已有协作约束")

        assert record is None

    @pytest.mark.asyncio
    async def test_user_prompt_includes_existing_evolutions_and_skill_content_when_store_available(self):
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps(
                    {
                        "need_patch": True,
                        "section": "Constraints",
                        "action": "append",
                        "content": "新增质量门",
                        "reason": "new_learning",
                    }
                )
            )
        )
        store = MagicMock()
        store.read_skill_content = AsyncMock(return_value="# Team Skill\n## Workflow\nExisting body")
        store.load_full_evolution_log = AsyncMock(
            return_value=SimpleNamespace(
                entries=[
                    SimpleNamespace(
                        id="ev_12345678",
                        change=SimpleNamespace(section="Collaboration", content="已有交接规则", skip_reason=None),
                    )
                ]
            )
        )
        optimizer = TeamSkillOptimizer(llm=llm, model="test-model", language="cn", evolution_store=store)

        trajectory = _MockTrajectory()
        record = await optimizer.generate_user_patch(trajectory, "test-skill", "补充质量门")

        assert record is not None
        prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
        assert "## 当前 Team Skill 正文" in prompt
        assert "Existing body" in prompt
        assert "已有演进经验摘要" in prompt
        assert "ev_12345678" in prompt

    @pytest.mark.asyncio
    async def test_retries_with_shorter_prompt_after_timeout(self):
        llm = MagicMock()
        optimizer = _make_optimizer(llm)
        llm.invoke = AsyncMock(
            side_effect=[
                asyncio.TimeoutError("request timed out"),
                SimpleNamespace(
                    content=json.dumps(
                        {
                            "section": "Collaboration",
                            "action": "append",
                            "content": "先同步上下文，再分派角色",
                        }
                    )
                ),
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

        response = json.dumps(
            {
                "need_patch": True,
                "section": "Constraints",
                "summary": "Cap execution timeout to avoid repeated stalls.",
                "content": "执行超时不得超过 30 秒",
                "reason": "轨迹显示多次超时",
            }
        )
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
        assert record.summary == "Cap execution timeout to avoid repeated stalls."
        assert "执行超时不得超过 30 秒" in record.change.content
        assert record.source == "team_skill_trajectory_patch"
        assert llm.invoke.await_args_list[0].kwargs["timeout"] == 120
        prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
        assert self._CURRENT_SKILL_CONTENT in prompt

    @pytest.mark.asyncio
    async def test_trajectory_prompt_includes_existing_evolutions_when_store_available(self):
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps(
                    {
                        "need_patch": True,
                        "section": "Collaboration",
                        "content": "补充交接确认",
                        "reason": "new_learning",
                    }
                )
            )
        )
        store = MagicMock()
        store.load_full_evolution_log = AsyncMock(
            return_value=SimpleNamespace(
                entries=[
                    SimpleNamespace(
                        id="ev_abcdef12",
                        change=SimpleNamespace(section="Workflow", content="已有流程约束", skip_reason=None),
                    )
                ]
            )
        )
        optimizer = TeamSkillOptimizer(llm=llm, model="test-model", language="cn", evolution_store=store)

        trajectory = _MockTrajectory()
        record = await optimizer.generate_trajectory_patch(
            trajectory,
            "test-skill",
            self._CURRENT_SKILL_CONTENT,
            [{"issue_type": "handoff", "description": "缺少确认", "severity": "high"}],
        )

        assert record is not None
        prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
        assert "已有演进经验摘要" in prompt
        assert "ev_abcdef12" in prompt

    @pytest.mark.asyncio
    async def test_uses_custom_record_llm_policy(self):
        """A custom record policy should override the default timeout."""
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps(
                    {
                        "need_patch": True,
                        "section": "Workflow",
                        "content": "Coordinate the reviewer handoff.",
                        "reason": "Observed repeated context loss.",
                    }
                )
            )
        )
        optimizer = TeamSkillOptimizer(
            llm=llm,
            model="test-model",
            language="en",
            record_llm_policy=LLMInvokePolicy(
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
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(content=json.dumps({"need_patch": False, "reason": "轨迹无异常"}))
        )

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
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps({"need_patch": True, "section": "Workflow", "content": "   "})
            )
        )

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
                SimpleNamespace(
                    content=json.dumps(
                        {
                            "need_patch": True,
                            "section": "Workflow",
                            "content": "失败后先收敛上下文，再重试",
                            "reason": "轨迹过长",
                        }
                    )
                ),
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


class TestGenerateRecords:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_signals(self):
        optimizer = _make_optimizer(MagicMock())

        records = await optimizer.generate_records(
            EvolutionContext(
                skill_name="test-skill",
                signals=[],
                skill_content="# Team Skill",
                messages=[],
                existing_desc_records=[],
                existing_body_records=[],
            )
        )

        assert records == []

    @pytest.mark.asyncio
    async def test_generate_reraises_llm_base_error(self):
        optimizer = _make_optimizer(MagicMock())
        optimizer._generate_drafts_with_retries = AsyncMock(
            side_effect=BaseError(StatusCode.COMPONENT_LLM_INVOKE_CALL_FAILED, error_msg="network failed")
        )

        with pytest.raises(BaseError):
            await optimizer.generate_records(
                EvolutionContext(
                    skill_name="test-skill",
                    signals=[
                        make_evolution_signal(
                            signal_type="trajectory_issue",
                            section="Workflow",
                            excerpt="handoff gap",
                            skill_name="test-skill",
                            source="passive_trajectory",
                        )
                    ],
                    skill_content="# Team Skill",
                    messages=[],
                    existing_desc_records=[],
                    existing_body_records=[],
                )
            )

    @pytest.mark.asyncio
    async def test_aggregates_user_and_trajectory_records_from_context(self):
        optimizer = _make_optimizer(MagicMock())
        optimizer.generate_user_patch = AsyncMock(return_value=SimpleNamespace(id="user-rec"))
        optimizer.generate_trajectory_patch = AsyncMock(return_value=SimpleNamespace(id="traj-rec"))
        trajectory = SimpleNamespace(steps=["x"])
        signals = [
            make_evolution_signal(
                signal_type="user_intent",
                section="Instructions",
                excerpt="增加 reviewer",
                skill_name="test-skill",
                source="explicit_request",
            ),
            make_evolution_signal(
                signal_type="trajectory_issue",
                section="",
                excerpt="issue",
                skill_name="test-skill",
                source="passive_trajectory",
                context={
                    "trajectory_issues": [{"issue_type": "handoff"}],
                    "skill_content": "# Current content",
                },
            ),
        ]

        records = await optimizer.generate_records(
            EvolutionContext(
                skill_name="test-skill",
                signals=signals,
                skill_content="# Fallback content",
                messages=[],
                existing_desc_records=[],
                existing_body_records=[],
                user_query="用户要求增加 reviewer",
                trajectory=trajectory,
            )
        )

        assert [record.id for record in records] == ["user-rec", "traj-rec"]
        optimizer.generate_user_patch.assert_awaited_once_with(
            trajectory,
            "test-skill",
            "增加 reviewer",
        )
        optimizer.generate_trajectory_patch.assert_awaited_once_with(
            trajectory,
            "test-skill",
            "# Current content",
            [{"issue_type": "handoff"}],
        )

    @pytest.mark.asyncio
    async def test_backward_routes_through_generate_records(self):
        optimizer = _make_optimizer(MagicMock())
        rec_1 = SimpleNamespace(id="rec-1")
        rec_2 = SimpleNamespace(id="rec-2")
        optimizer.generate_records = AsyncMock(return_value=[rec_1, rec_2])
        operator = MagicMock()
        operator.get_tunables.return_value = ["experiences"]
        signal = make_evolution_signal(
            signal_type="user_intent",
            section="Instructions",
            excerpt="please improve",
            skill_name="test-skill",
            source="explicit_request",
        )
        online_ctx = OnlineEvolutionContext(
            skill_name="test-skill",
            signals=[signal],
            messages=[{"role": "user", "content": "hello"}],
            user_query="please improve",
            skill_content="# Team Skill",
            existing_desc_records=["desc-rec"],
            existing_body_records=["body-rec"],
            existing_script_records=["script-rec"],
        )

        optimizer.bind(
            {"skill_experience_test-skill": operator},
            targets=["experiences"],
            online_contexts={"test-skill": online_ctx},
        )
        optimizer.add_trajectory(
            SimpleNamespace(
                execution_id="exec-1",
                session_id="session-1",
                source="online",
                steps=[],
            )
        )
        await optimizer.backward([signal])

        call_ctx = optimizer.generate_records.await_args.args[0]
        assert call_ctx.skill_name == "test-skill"
        assert call_ctx.signals == [signal]
        assert call_ctx.skill_content == "# Team Skill"
        assert call_ctx.messages == [{"role": "user", "content": "hello"}]
        assert call_ctx.existing_desc_records == ["desc-rec"]
        assert call_ctx.existing_body_records == ["body-rec"]
        assert call_ctx.existing_script_records == ["script-rec"]
        assert call_ctx.user_query == "please improve"
        assert call_ctx.trajectory.session_id == "session-1"
        assert optimizer.parameters()["skill_experience_test-skill"].get_gradient("experiences") == [rec_1, rec_2]

    @pytest.mark.asyncio
    async def test_backward_prefers_explicit_online_context(self):
        optimizer = _make_optimizer(MagicMock())
        rec = SimpleNamespace(id="rec-1")
        optimizer.generate_records = AsyncMock(return_value=[rec])
        operator = MagicMock()
        operator.get_tunables.return_value = {"experiences": object()}
        signal = make_evolution_signal(
            signal_type="user_intent",
            section="Instructions",
            excerpt="please improve",
            skill_name="test-skill",
            source="explicit_request",
        )
        trajectory = SimpleNamespace(
            execution_id="exec-2",
            session_id="session-2",
            source="online",
            steps=[],
        )
        online_ctx = OnlineEvolutionContext(
            skill_name="test-skill",
            signals=[signal],
            messages=[{"role": "user", "content": "context"}],
            user_query="context query",
            skill_content="# Context Team Skill",
            existing_desc_records=["ctx-desc"],
            existing_body_records=["ctx-body"],
            existing_script_records=["ctx-script"],
            trajectory=trajectory,
        )

        optimizer.bind(
            {"skill_experience_test-skill": operator},
            targets=["experiences"],
            online_contexts={"test-skill": online_ctx},
        )
        await optimizer.backward([signal])

        call_ctx = optimizer.generate_records.await_args.args[0]
        assert call_ctx is online_ctx
        assert call_ctx.skill_content == "# Context Team Skill"
        assert call_ctx.messages == [{"role": "user", "content": "context"}]
        assert call_ctx.existing_desc_records == ["ctx-desc"]
        assert call_ctx.existing_body_records == ["ctx-body"]
        assert call_ctx.existing_script_records == ["ctx-script"]
        assert call_ctx.user_query == "context query"
        assert call_ctx.trajectory.session_id == "session-2"

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_raises_clear_error_without_online_context():
        optimizer = _make_optimizer(MagicMock())
        operator = MagicMock()
        operator.get_tunables.return_value = {"experiences": object()}
        signal = make_evolution_signal(
            signal_type="user_intent",
            section="Instructions",
            excerpt="please improve",
            skill_name="test-skill",
            source="explicit_request",
        )

        optimizer.bind({"skill_experience_test-skill": operator}, targets=["experiences"])
        with pytest.raises(BaseError, match="online_contexts missing entry for skill test-skill"):
            await optimizer.backward([signal])

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_does_not_mutate_online_context_when_filling_default_trajectory():
        optimizer = _make_optimizer(MagicMock())
        rec = SimpleNamespace(id="rec-1")
        optimizer.generate_records = AsyncMock(return_value=[rec])
        operator = MagicMock()
        operator.get_tunables.return_value = {"experiences": object()}
        signal = make_evolution_signal(
            signal_type="trajectory_issue",
            section="Workflow",
            excerpt="handoff gap",
            skill_name="test-skill",
            source="passive_trajectory",
        )
        online_ctx = OnlineEvolutionContext(
            skill_name="test-skill",
            signals=[signal],
            messages=[],
            user_query="",
            skill_content="# Context Team Skill",
            existing_desc_records=[],
            existing_body_records=[],
            existing_script_records=[],
            trajectory=None,
        )

        optimizer.bind(
            {"skill_experience_test-skill": operator},
            targets=["experiences"],
            online_contexts={"test-skill": online_ctx},
        )
        optimizer.add_trajectory(
            SimpleNamespace(
                execution_id="exec-3",
                session_id="session-3",
                source="online",
                steps=[],
            )
        )

        await optimizer.backward([signal])

        call_ctx = optimizer.generate_records.await_args.args[0]
        assert call_ctx is not online_ctx
        assert call_ctx.trajectory.session_id == "session-3"
        assert online_ctx.trajectory is None

    @pytest.mark.asyncio
    async def test_generate_repairs_malformed_json_and_emits_description_target(self):
        llm = MagicMock()
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    content='[{"action":"append","target":"description","section":"Instructions","content":"bad",]'
                ),
                SimpleNamespace(
                    content='[{"action":"append","target":"description","section":"Instructions","content":"Add clearer team applicability wording."}]'
                ),
            ]
        )
        optimizer = _make_optimizer(llm, language="en")

        records = await optimizer.generate_records(
            EvolutionContext(
                skill_name="test-skill",
                signals=[
                    make_evolution_signal(
                        signal_type="user_intent",
                        section="Instructions",
                        excerpt="clarify when to use this team",
                        skill_name="test-skill",
                        source="explicit_request",
                    )
                ],
                skill_content="# Team Skill",
                messages=[],
                existing_desc_records=[],
                existing_body_records=[],
            )
        )

        assert len(records) == 1
        assert records[0].change.target == EvolutionTarget.DESCRIPTION
        assert records[0].change.section == "Instructions"
        assert llm.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_generate_regenerates_when_output_is_truncated(self):
        llm = MagicMock()
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(content='[{"action":"append","target":"body","section":"Workflow","content":"cut off"'),
                SimpleNamespace(
                    content='[{"action":"append","target":"body","section":"Workflow","content":"### Workflow\\n- Add an explicit handoff gate."}]'
                ),
            ]
        )
        optimizer = _make_optimizer(llm, language="en")

        records = await optimizer.generate_records(
            EvolutionContext(
                skill_name="test-skill",
                signals=[
                    make_evolution_signal(
                        signal_type="trajectory_issue",
                        section="",
                        excerpt="handoff gap",
                        skill_name="test-skill",
                        source="passive_trajectory",
                        context={"trajectory_issues": [{"issue_type": "handoff"}]},
                    )
                ],
                skill_content="# Team Skill",
                messages=[],
                existing_desc_records=[],
                existing_body_records=[],
            )
        )

        assert len(records) == 1
        assert records[0].change.section == "Workflow"
        assert llm.invoke.await_count == 2
        first_prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
        second_prompt = llm.invoke.await_args_list[1].kwargs["messages"][0]["content"]
        assert second_prompt == first_prompt

    @pytest.mark.asyncio
    async def test_generate_supports_script_target_and_limits_text_records(self):
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps(
                    [
                        {
                            "action": "append",
                            "target": "body",
                            "section": "Workflow",
                            "summary": "Add a handoff gate before review starts.",
                            "content": "### Workflow\n- Rule A",
                        },
                        {
                            "action": "append",
                            "target": "description",
                            "section": "Instructions",
                            "content": "Clarify the team specializes in review-heavy tasks.",
                        },
                        {
                            "action": "append",
                            "target": "body",
                            "section": "Collaboration",
                            "content": "### Collaboration\n- Rule C",
                        },
                        {
                            "action": "append",
                            "target": "script",
                            "section": "Scripts",
                            "summary": "Audit team handoff completeness with a helper script.",
                            "content": "print('hello')",
                            "script_filename": "handoff_audit.py",
                            "script_language": "python",
                            "script_purpose": "audit handoff completeness",
                        },
                    ]
                )
            )
        )
        optimizer = _make_optimizer(llm, language="en")

        records = await optimizer.generate_records(
            EvolutionContext(
                skill_name="test-skill",
                signals=[
                    make_evolution_signal(
                        signal_type="trajectory_issue",
                        section="",
                        excerpt="need reusable handoff audit",
                        skill_name="test-skill",
                        source="passive_trajectory",
                    )
                ],
                skill_content="# Team Skill",
                messages=[],
                existing_desc_records=[],
                existing_body_records=[],
            )
        )

        assert len(records) == 3
        text_records = [record for record in records if record.change.target != EvolutionTarget.SCRIPT]
        script_records = [record for record in records if record.change.target == EvolutionTarget.SCRIPT]
        assert len(text_records) == 2
        assert len(script_records) == 1
        assert text_records[0].summary == "Add a handoff gate before review starts."
        assert script_records[0].summary == "Audit team handoff completeness with a helper script."
        assert script_records[0].change.script_filename == "handoff_audit.py"
