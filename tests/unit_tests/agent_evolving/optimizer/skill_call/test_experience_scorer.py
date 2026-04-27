# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Unit tests for ExperienceScorer and E/U/F scoring functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
    UsageStats,
)
from openjiuwen.agent_evolving.optimizer.skill_call.experience_scorer import (
    FRESHNESS_HALF_LIFE_DAYS,
    STALE_VERSION_PENALTY,
    W_E,
    W_F,
    W_U,
    ExperienceScorer,
    calc_effectiveness,
    calc_freshness,
    calc_score,
    calc_utilization,
    update_score,
)


def _make_record(
    *,
    score: float = 0.6,
    usage_stats: UsageStats | None = None,
    timestamp: str | None = None,
    skill_version: str | None = None,
) -> EvolutionRecord:
    record = EvolutionRecord.make(
        source="test",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content="test content",
            target=EvolutionTarget.BODY,
        ),
        score=score,
    )
    if usage_stats is not None:
        record.usage_stats = usage_stats
    if timestamp is not None:
        record.timestamp = timestamp
    if skill_version is not None:
        record.skill_version = skill_version
    return record


# ---------------------------------------------------------------------------
# calc_effectiveness
# ---------------------------------------------------------------------------


class TestCalcEffectiveness:
    def test_no_data_returns_neutral(self):
        stats = UsageStats()  # all zeros
        assert calc_effectiveness(stats) == pytest.approx(0.5)

    def test_all_positive(self):
        stats = UsageStats(times_positive=10, times_negative=0)
        result = calc_effectiveness(stats)
        # Bayesian: (10+1)/(10+2) = 11/12 ≈ 0.917
        assert result == pytest.approx(11 / 12, abs=1e-9)

    def test_all_negative(self):
        stats = UsageStats(times_positive=0, times_negative=10)
        result = calc_effectiveness(stats)
        # Bayesian: (0+1)/(10+2) = 1/12 ≈ 0.083
        assert result == pytest.approx(1 / 12, abs=1e-9)

    def test_mixed(self):
        stats = UsageStats(times_positive=5, times_negative=5)
        result = calc_effectiveness(stats)
        # Bayesian: (5+1)/(10+2) = 6/12 = 0.5
        assert result == pytest.approx(0.5)

    def test_single_positive(self):
        stats = UsageStats(times_positive=1, times_negative=0)
        result = calc_effectiveness(stats)
        # (1+1)/(1+2) = 2/3 ≈ 0.667
        assert result == pytest.approx(2 / 3, abs=1e-9)


# ---------------------------------------------------------------------------
# calc_utilization
# ---------------------------------------------------------------------------


class TestCalcUtilization:
    def test_no_presentations_returns_neutral(self):
        stats = UsageStats(times_presented=0, times_used=0)
        assert calc_utilization(stats) == pytest.approx(0.5)

    def test_never_used(self):
        stats = UsageStats(times_presented=10, times_used=0)
        assert calc_utilization(stats) == pytest.approx(0.0)

    def test_always_used(self):
        stats = UsageStats(times_presented=5, times_used=5)
        assert calc_utilization(stats) == pytest.approx(1.0)

    def test_half_used(self):
        stats = UsageStats(times_presented=10, times_used=5)
        assert calc_utilization(stats) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# calc_freshness
# ---------------------------------------------------------------------------


class TestCalcFreshness:
    def test_recent_record_high_freshness(self):
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        record = _make_record(timestamp=now_ts)
        result = calc_freshness(record)
        # Brand new → freshness close to 1.0
        assert result > 0.95

    def test_very_old_record_low_freshness(self):
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=365)).isoformat()
        record = _make_record(timestamp=old_ts)
        result = calc_freshness(record)
        # 1 year old (≈4 half-lives) → freshness near 0.5 + 0.5*(2^-4) ≈ 0.531
        assert 0.5 < result < 0.6

    def test_malformed_timestamp_returns_neutral(self):
        record = _make_record(timestamp="not-a-date")
        assert calc_freshness(record) == pytest.approx(0.5)

    def test_empty_timestamp_returns_neutral(self):
        record = _make_record(timestamp="")
        assert calc_freshness(record) == pytest.approx(0.5)

    def test_stale_version_applies_penalty(self):
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        record = _make_record(timestamp=now_ts, skill_version="1.0.0")
        current_version = "2.0.0"
        result_stale = calc_freshness(record, current_skill_version=current_version)
        result_current = calc_freshness(record, current_skill_version="1.0.0")
        assert result_stale == pytest.approx(result_current * STALE_VERSION_PENALTY, abs=1e-6)

    def test_matching_version_no_penalty(self):
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        record = _make_record(timestamp=now_ts, skill_version="1.0.0")
        result = calc_freshness(record, current_skill_version="1.0.0")
        assert result > 0.95

    def test_freshness_half_life(self):
        half_life_ts = (datetime.now(tz=timezone.utc) - timedelta(days=FRESHNESS_HALF_LIFE_DAYS)).isoformat()
        record = _make_record(timestamp=half_life_ts)
        result = calc_freshness(record)
        # At exactly half-life: 0.5 + 0.5*(2^-1) = 0.75
        assert result == pytest.approx(0.75, abs=0.01)


# ---------------------------------------------------------------------------
# calc_score
# ---------------------------------------------------------------------------


class TestCalcScore:
    def test_weights_sum_to_one(self):
        assert W_E + W_U + W_F == pytest.approx(1.0)

    def test_score_with_default_stats(self):
        record = _make_record()
        score = calc_score(record)
        # E=0.5 (no data), U=0.5 (no data), F≈1.0 (brand new)
        # score = 0.5*0.5 + 0.3*0.5 + 0.2*F ≈ 0.25 + 0.15 + 0.2 = 0.6
        assert 0.5 < score < 0.75

    def test_high_usage_positive_record_scores_high(self):
        stats = UsageStats(
            times_presented=10,
            times_used=9,
            times_positive=8,
            times_negative=1,
        )
        record = _make_record(usage_stats=stats)
        score = calc_score(record)
        assert score > 0.7

    def test_unused_old_record_scores_low(self):
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=730)).isoformat()
        stats = UsageStats(times_presented=5, times_used=0, times_negative=5)
        record = _make_record(usage_stats=stats, timestamp=old_ts)
        score = calc_score(record)
        assert score < 0.45


# ---------------------------------------------------------------------------
# update_score
# ---------------------------------------------------------------------------


class TestUpdateScore:
    def test_positive_result_increases_score(self):
        stats = UsageStats(times_presented=5, times_used=3, times_positive=3)
        record = _make_record(usage_stats=stats)
        update_score(record, {"used": True, "positive": True, "negative": False})
        assert record.usage_stats.times_used == 4
        assert record.usage_stats.times_positive == 4
        assert record.usage_stats.last_evaluated_at is not None

    def test_negative_result_updates_stats(self):
        stats = UsageStats(times_presented=5, times_used=2)
        record = _make_record(usage_stats=stats)
        update_score(record, {"used": False, "positive": False, "negative": True})
        assert record.usage_stats.times_negative == 1
        assert record.usage_stats.times_used == 2  # unchanged

    def test_none_usage_stats_initialized(self):
        record = _make_record()
        record.usage_stats = None
        update_score(record, {"used": True, "positive": True, "negative": False})
        assert record.usage_stats is not None
        assert record.usage_stats.times_used == 1

    def test_returns_new_score(self):
        record = _make_record()
        result = update_score(record, {"used": False, "positive": False, "negative": False})
        assert isinstance(result, float)
        assert result == record.score


# ---------------------------------------------------------------------------
# ExperienceScorer
# ---------------------------------------------------------------------------


class TestExperienceScorerEvaluate:
    def _make_scorer(self, response_json: str) -> ExperienceScorer:
        llm = Mock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=response_json))
        return ExperienceScorer(llm=llm, model="test-model", language="en")

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_records(self):
        scorer = self._make_scorer("[]")
        result = await scorer.evaluate("snippet", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_parses_valid_response(self):
        response = '[{"record_id": "ev_abc", "used": true, "positive": true, "negative": false, "reason": "good"}]'
        scorer = self._make_scorer(response)
        record = _make_record()
        record.id = "ev_abc"
        result = await scorer.evaluate("conversation snippet", [record])
        assert len(result) == 1
        assert result[0]["record_id"] == "ev_abc"
        assert result[0]["used"] is True

    @pytest.mark.asyncio
    async def test_returns_empty_on_llm_error(self):
        llm = Mock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        scorer = ExperienceScorer(llm=llm, model="test", language="en")
        result = await scorer.evaluate("snippet", [_make_record()])
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_invalid_json(self):
        scorer = self._make_scorer("not json at all")
        result = await scorer.evaluate("snippet", [_make_record()])
        assert result == []

    @pytest.mark.asyncio
    async def test_retries_when_first_response_is_unparseable(self):
        llm = Mock()
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(content="not json at all"),
                SimpleNamespace(content='[{"record_id":"ev_abc","used":true,"positive":false,"negative":false}]'),
            ]
        )
        scorer = ExperienceScorer(llm=llm, model="test", language="en")
        record = _make_record()
        record.id = "ev_abc"

        result = await scorer.evaluate("snippet", [record])

        assert len(result) == 1
        assert result[0]["record_id"] == "ev_abc"
        assert llm.invoke.await_count == 2


class TestExperienceScorerSimplify:
    def _make_scorer(self, response_json: str) -> ExperienceScorer:
        llm = Mock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content=response_json))
        return ExperienceScorer(llm=llm, model="test-model", language="en")

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_records(self):
        scorer = self._make_scorer("[]")
        result = await scorer.simplify("skill-a", "summary", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_parses_valid_simplify_response(self):
        response = '[{"action": "DELETE", "record_id": "ev_old", "reason": "stale"}]'
        scorer = self._make_scorer(response)
        record = _make_record()
        result = await scorer.simplify("skill-a", "summary", [record])
        assert len(result) == 1
        assert result[0]["action"] == "DELETE"

    @pytest.mark.asyncio
    async def test_returns_empty_on_llm_error(self):
        llm = Mock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        scorer = ExperienceScorer(llm=llm, model="test", language="cn")
        result = await scorer.simplify("skill-a", "summary", [_make_record()])
        assert result == []

    @pytest.mark.asyncio
    async def test_retries_when_first_simplify_response_is_unparseable(self):
        llm = Mock()
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(content="not json at all"),
                SimpleNamespace(content='[{"action":"KEEP","record_id":"ev_1","reason":"ok"}]'),
            ]
        )
        scorer = ExperienceScorer(llm=llm, model="test", language="cn")

        result = await scorer.simplify("skill-a", "summary", [_make_record()])

        assert len(result) == 1
        assert result[0]["action"] == "KEEP"
        assert llm.invoke.await_count == 2


class TestExecuteSimplifyActions:
    @pytest.mark.asyncio
    async def test_delete_action(self):
        store = Mock()
        store.delete_records = AsyncMock(return_value=1)
        scorer = ExperienceScorer(llm=Mock(), model="test", language="en")
        actions = [{"action": "DELETE", "record_id": "ev_001", "reason": "old"}]
        counts = await scorer.execute_simplify_actions(store, "skill-a", actions)
        store.delete_records.assert_called_once_with("skill-a", ["ev_001"])
        assert counts["deleted"] == 1

    @pytest.mark.asyncio
    async def test_merge_action(self):
        record = _make_record()
        store = Mock()
        store.merge_records = AsyncMock(return_value=record)
        scorer = ExperienceScorer(llm=Mock(), model="test", language="en")
        actions = [
            {
                "action": "MERGE",
                "record_id": "ev_001",
                "merge_remove_ids": ["ev_002", "ev_003"],
                "new_content": "merged content",
                "reason": "similar",
            }
        ]
        counts = await scorer.execute_simplify_actions(store, "skill-a", actions)
        store.merge_records.assert_called_once_with("skill-a", "ev_001", ["ev_002", "ev_003"], "merged content")
        assert counts["merged"] == 1

    @pytest.mark.asyncio
    async def test_refine_action(self):
        record = _make_record()
        store = Mock()
        store.update_record_content = AsyncMock(return_value=record)
        scorer = ExperienceScorer(llm=Mock(), model="test", language="en")
        actions = [{"action": "REFINE", "record_id": "ev_001", "new_content": "better", "reason": "vague"}]
        counts = await scorer.execute_simplify_actions(store, "skill-a", actions)
        store.update_record_content.assert_called_once_with("skill-a", "ev_001", "better")
        assert counts["refined"] == 1

    @pytest.mark.asyncio
    async def test_keep_action(self):
        store = Mock()
        scorer = ExperienceScorer(llm=Mock(), model="test", language="en")
        actions = [{"action": "KEEP", "record_id": "ev_001", "reason": "good"}]
        counts = await scorer.execute_simplify_actions(store, "skill-a", actions)
        assert counts["kept"] == 1

    @pytest.mark.asyncio
    async def test_delete_not_found_counts_as_error(self):
        store = Mock()
        store.delete_records = AsyncMock(return_value=0)
        scorer = ExperienceScorer(llm=Mock(), model="test", language="en")
        actions = [{"action": "DELETE", "record_id": "ev_missing"}]
        counts = await scorer.execute_simplify_actions(store, "skill-a", actions)
        assert counts["errors"] == 1

    @pytest.mark.asyncio
    async def test_exception_in_action_counted_as_error(self):
        store = Mock()
        store.delete_records = AsyncMock(side_effect=OSError("disk full"))
        scorer = ExperienceScorer(llm=Mock(), model="test", language="en")
        actions = [{"action": "DELETE", "record_id": "ev_001"}]
        counts = await scorer.execute_simplify_actions(store, "skill-a", actions)
        assert counts["errors"] == 1

    @pytest.mark.asyncio
    async def test_unknown_action_counted_as_error(self):
        store = Mock()
        scorer = ExperienceScorer(llm=Mock(), model="test", language="en")
        actions = [{"action": "UNKNOWN", "record_id": "ev_001"}]
        counts = await scorer.execute_simplify_actions(store, "skill-a", actions)
        assert counts["errors"] == 1

    @pytest.mark.asyncio
    async def test_empty_actions_returns_zero_counts(self):
        store = Mock()
        scorer = ExperienceScorer(llm=Mock(), model="test", language="en")
        counts = await scorer.execute_simplify_actions(store, "skill-a", [])
        assert all(v == 0 for v in counts.values())


class TestExperienceScorerUpdateLlm:
    def test_update_llm_replaces_internal_state(self):
        old_llm = Mock()
        scorer = ExperienceScorer(llm=old_llm, model="old-model", language="en")
        new_llm = Mock()
        scorer.update_llm(new_llm, "new-model")
        assert scorer._llm is new_llm
        assert scorer._model == "new-model"


class TestExperienceScorerFormatHelpers:
    def test_format_presented_experiences(self):
        record = _make_record()
        record.id = "ev_test01"
        result = ExperienceScorer._format_presented_experiences([record])
        assert "ev_test01" in result
        assert "test content" in result

    def test_format_scored_experiences(self):
        stats = UsageStats(times_presented=3, times_used=2)
        record = _make_record(score=0.75, usage_stats=stats)
        record.id = "ev_test02"
        result = ExperienceScorer._format_scored_experiences([record])
        assert "ev_test02" in result
        assert "0.75" in result
        assert "presented=3" in result


class TestExperienceScorerSimplifyNewSections:
    """Confirm simplify() accepts arbitrary section types (not hardcoded)."""

    @pytest.mark.asyncio
    async def test_simplify_accepts_collaboration_roles_constraints(self):
        """simplify 方法应该能处理包含新 section 的记录。"""
        patches = [
            EvolutionPatch(
                section="Collaboration",
                action="append",
                content="与目标角色协作：传递结果",
                target=EvolutionTarget.BODY,
            ),
            EvolutionPatch(
                section="Roles",
                action="append",
                content="增加 reviewer 角色",
                target=EvolutionTarget.BODY,
            ),
            EvolutionPatch(
                section="Constraints",
                action="append",
                content="执行时间不超过 10 分钟",
                target=EvolutionTarget.BODY,
            ),
        ]
        records = [EvolutionRecord.make(source="test", context="test", change=p) for p in patches]

        llm = Mock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="[]"))
        scorer = ExperienceScorer(llm=llm, model="test-model", language="cn")

        result = await scorer.simplify(
            skill_name="test-skill",
            skill_summary="test summary",
            records=records,
        )
        assert isinstance(result, list)
        # LLM was invoked — confirms no section-name gating exists
        assert llm.invoke.call_count == 1
