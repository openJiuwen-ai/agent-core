# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Templates shared verbatim by every member must not name leader-only tools.

``TeamPolicyRail`` keeps the static sections identical across members (prefix
KV-cache stability), so a template every member reads cannot name a tool only
the leader holds: naming a tool the reader cannot call sends it hunting for
that tool (prompts/AGENTS.md editing rule 2 — a readable-but-uncallable tool
is worse than an absent mention).
"""

import pytest

from openjiuwen.agent_teams.prompts.loader import load_template


@pytest.mark.level1
@pytest.mark.parametrize("language", ["cn", "en"])
def test_inbound_tags_do_not_name_the_build_team_tool(language: str):
    notice = load_template("inbound_tags", language).content

    assert "build_team" not in notice


@pytest.mark.level1
@pytest.mark.parametrize("language", ["cn", "en"])
def test_anonymous_hitt_contract_does_not_direct_members_to_update_task(language: str):
    # ``update_task`` is leader-only: autonomous members never hold it, and a
    # scheduled member only gets the subtask-scoped variant behind the
    # member-subtasks gate. The anonymous contract is the default teammate
    # HITT section, so "submit an update_task" points at a tool it lacks.
    contract = load_template("hitt_teammate_anonymous", language).content

    assert "update_task" not in contract
