# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for openjiuwen.extensions.context_evolver.offline_memory.l3.

Covers the pure playbook-mutation logic (apply_actions,
apply_deprecation_pass, apply_merges — no LLM involved) and the two LLM
call sites (propose_actions, propose_merges) via the shared MockLLMModel
fixture.
"""

from __future__ import annotations

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.extensions.context_evolver.offline_memory.l3 import (
    L3MergeGroup,
    apply_actions,
    apply_deprecation_pass,
    apply_merges,
    propose_actions,
    propose_merges,
)

from tests.unit_tests.fixtures.mock_llm import create_json_response, create_text_response, mock_llm_context


def _model() -> Model:
    return Model(
        ModelClientConfig(client_provider="OpenAI", api_key="mock-api-key", api_base="http://mock", verify_ssl=False),
        ModelRequestConfig(model_name="mock-model"),
    )


class TestApplyActions:
    def test_new_action_creates_item(self) -> None:
        playbook = {"items": {}}
        actions = [
            {
                "verdict": "new",
                "category": "workflow",
                "description": "when N roles write independent sections, add an integration pass",
            }
        ]
        playbook, counts = apply_actions(playbook, actions, "sample_1", "general")
        assert counts == {"reinforced": 0, "contradicted": 0, "added": 1, "skipped": 0}
        (item,) = playbook["items"].values()
        assert item["support_count"] == 1
        assert item["evidence_sample_ids"] == ["sample_1"]
        assert item["status"] == "active"

    def test_reinforce_bumps_support_count(self) -> None:
        playbook = {"items": {"abc123": {"support_count": 1, "contradict_count": 0, "evidence_sample_ids": ["s0"]}}}
        actions = [{"verdict": "reinforce", "item_id": "abc123"}]
        playbook, counts = apply_actions(playbook, actions, "s1", "general")
        assert playbook["items"]["abc123"]["support_count"] == 2
        assert counts["reinforced"] == 1

    def test_contradict_unknown_item_id_is_skipped(self) -> None:
        playbook = {"items": {}}
        actions = [{"verdict": "contradict", "item_id": "does_not_exist"}]
        _, counts = apply_actions(playbook, actions, "s1", "general")
        assert counts["skipped"] == 1

    def test_unknown_verdict_is_skipped(self) -> None:
        playbook = {"items": {}}
        _, counts = apply_actions(playbook, [{"verdict": "bogus"}], "s1", "general")
        assert counts["skipped"] == 1

    def test_new_agent_change_syncs_taxonomy(self, tmp_path) -> None:
        taxonomy_path = tmp_path / "role_taxonomy.yaml"
        playbook = {"items": {}}
        actions = [
            {
                "verdict": "new",
                "category": "agent_change",
                "action": "include",
                "recommended_role_type": "fact_researcher",
                "trigger_condition": "task needs independent fact-checking",
                "expertise": "Verifies factual claims against sources.",
            }
        ]
        apply_actions(playbook, actions, "s1", "general", taxonomy_path)
        from openjiuwen.extensions.context_evolver.offline_memory import bank_io

        taxonomy = bank_io.load_yaml(taxonomy_path)
        assert taxonomy["fact_researcher"] == "Verifies factual claims against sources."

    def test_never_rewrites_description_on_reinforce(self) -> None:
        playbook = {"items": {"abc123": {"description": "original text", "support_count": 1, "contradict_count": 0}}}
        apply_actions(playbook, [{"verdict": "reinforce", "item_id": "abc123"}], "s1", "general")
        assert playbook["items"]["abc123"]["description"] == "original text"


class TestApplyDeprecationPass:
    def test_deprecates_when_contradictions_dominate(self) -> None:
        items = {"a": {"status": "active", "support_count": 1, "contradict_count": 2}}
        deprecated = apply_deprecation_pass(items)
        assert deprecated == ["a"]
        assert items["a"]["status"] == "deprecated"

    def test_leaves_active_when_below_min_samples(self) -> None:
        items = {"a": {"status": "active", "support_count": 0, "contradict_count": 1}}
        assert apply_deprecation_pass(items) == []
        assert items["a"]["status"] == "active"

    def test_leaves_active_when_support_dominates(self) -> None:
        items = {"a": {"status": "active", "support_count": 5, "contradict_count": 1}}
        assert apply_deprecation_pass(items) == []


class TestApplyMerges:
    def test_merges_sum_counters_and_union_evidence(self) -> None:
        items = {
            "keep": {"status": "active", "support_count": 2, "contradict_count": 0, "evidence_sample_ids": ["s1"]},
            "dupe": {"status": "active", "support_count": 1, "contradict_count": 1, "evidence_sample_ids": ["s2"]},
        }
        merges = [{"keep_id": "keep", "merge_ids": ["dupe"], "merged_description": "consolidated"}]
        applied = apply_merges(items, merges)
        assert applied == [("keep", ["dupe"])]
        assert items["keep"]["support_count"] == 3
        assert items["keep"]["contradict_count"] == 1
        assert set(items["keep"]["evidence_sample_ids"]) == {"s1", "s2"}
        assert items["keep"]["description"] == "consolidated"
        assert items["dupe"]["status"] == "deprecated"
        assert items["dupe"]["merged_into"] == "keep"

    def test_skips_group_with_missing_keep_id(self) -> None:
        items = {"a": {"status": "active"}}
        applied = apply_merges(items, [{"keep_id": "missing", "merge_ids": ["a"]}])
        assert applied == []

    def test_accepts_typed_merge_groups_from_propose_merges(self) -> None:
        items = {
            "keep": {"status": "active", "support_count": 1, "contradict_count": 0, "evidence_sample_ids": ["s1"]},
            "dupe": {"status": "active", "support_count": 1, "contradict_count": 0, "evidence_sample_ids": ["s2"]},
        }
        merges = [L3MergeGroup(keep_id="keep", merge_ids=["dupe"], merged_description="combined")]

        applied = apply_merges(items, merges)

        assert applied == [("keep", ["dupe"])]
        assert items["keep"]["support_count"] == 2
        assert items["keep"]["description"] == "combined"
        assert items["dupe"]["merged_into"] == "keep"


class TestProposeActions:
    @pytest.mark.asyncio
    async def test_parses_actions_list(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_json_response({"actions": [{"verdict": "new", "category": "workflow"}]})])
            result = await propose_actions(
                _model(),
                task_category="general",
                mode="dynamic",
                episode_evidence_block="evidence",
                playbook_block="(playbook is currently empty)",
            )
        assert len(result.actions) == 1
        assert result.actions[0]["verdict"] == "new"

    @pytest.mark.asyncio
    async def test_empty_actions_is_normal(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_json_response({"actions": []})])
            result = await propose_actions(
                _model(),
                task_category="general",
                mode="dynamic",
                episode_evidence_block="evidence",
                playbook_block="(empty)",
            )
        assert result.actions == []

    @pytest.mark.asyncio
    async def test_raises_after_exhausting_retries(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_text_response("garbage") for _ in range(2)])
            with pytest.raises(BaseError):
                await propose_actions(
                    _model(),
                    task_category="general",
                    mode="dynamic",
                    episode_evidence_block="evidence",
                    playbook_block="(empty)",
                    retries=2,
                )


class TestProposeMerges:
    @pytest.mark.asyncio
    async def test_parses_merge_groups(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses(
                [
                    create_json_response(
                        {"merges": [{"keep_id": "a", "merge_ids": ["b"], "merged_description": "combined"}]}
                    )
                ]
            )
            result = await propose_merges(_model(), task_category="general", items_block="- id=a ...\n- id=b ...")
        assert len(result.merges) == 1
        assert result.merges[0].keep_id == "a"
        assert result.merges[0].merge_ids == ["b"]

    @pytest.mark.asyncio
    async def test_empty_merges_is_normal(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_json_response({"merges": []})])
            result = await propose_merges(_model(), task_category="general", items_block="(no active items)")
        assert result.merges == []
