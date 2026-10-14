# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Completion is handed over through the task row, never a send_message report (F_119 / F_120)."""

import pytest

from openjiuwen.agent_teams.prompts.loader import load_template


@pytest.mark.level0
@pytest.mark.parametrize("language", ["cn", "en"])
def test_general_workflow_no_longer_asks_for_a_completion_report(language: str):
    policy = load_template("teammate_policy", language).content

    assert "summary" in policy and "note" in policy
    assert "完成后及时通知依赖方" not in policy
    assert "notify dependents promptly upon completion" not in policy
    assert "向 Leader 发送完成汇报" not in policy
    assert "send a completion report to Leader" not in policy


@pytest.mark.level0
@pytest.mark.parametrize("language", ["cn", "en"])
@pytest.mark.parametrize("dispatch", ["dispatch_autonomous_teammate", "dispatch_scheduled_teammate"])
def test_both_dispatch_sections_hand_over_through_summary_and_note(language: str, dispatch: str):
    section = load_template(dispatch, language).content

    assert "summary" in section and "note" in section
    if language == "cn":
        assert "**不要**用 `send_message` 汇报完成" in section
    else:
        assert "do **not** `send_message` to report completion" in section


@pytest.mark.level1
@pytest.mark.parametrize("language", ["cn", "en"])
def test_scheduled_section_drops_the_override_clause(language: str):
    section = load_template("dispatch_scheduled_teammate", language).content

    assert "优先于通用工作流程" not in section
    assert "overrides the" not in section
