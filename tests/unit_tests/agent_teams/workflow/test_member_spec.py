# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the shared member-spec derivation used by both swarmflow backends."""
from __future__ import annotations

from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec, ProgressiveToolSpec
from openjiuwen.agent_teams.workflow.backends._member_spec import derive_member_spec


def test_derive_member_spec_preserves_progressive_tool():
    """The per-member copy overrides card/model/prompt/tools only; the team's
    progressive-tool opt-in rides through untouched to every worker and session."""
    base = DeepAgentSpec(tools=[], progressive_tool=ProgressiveToolSpec(enabled=True))

    derived = derive_member_spec(
        base,
        team_name="wf_team",
        member_name="wf-r1-coder-abc123",
        system_prompt="do the thing",
        model=None,
    )

    assert derived.progressive_tool == ProgressiveToolSpec(enabled=True)
    assert derived.card.name == "wf-r1-coder-abc123"
    assert base.progressive_tool == ProgressiveToolSpec(enabled=True)
