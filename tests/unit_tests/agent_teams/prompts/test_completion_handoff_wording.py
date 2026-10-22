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


@pytest.mark.level1
@pytest.mark.parametrize("language", ["cn", "en"])
@pytest.mark.parametrize("dispatch", ["dispatch_autonomous_leader", "dispatch_scheduled_leader"])
def test_leader_is_told_not_to_ask_members_for_completion_reports(language: str, dispatch: str):
    section = load_template(dispatch, language).content

    if language == "cn":
        assert "**不要**在任务内容里要求成员完成后用 `send_message` 汇报" in section
    else:
        assert "do **not** write into a task's content that the member must `send_message` you" in section


@pytest.mark.level1
@pytest.mark.parametrize("language", ["cn", "en"])
@pytest.mark.parametrize("desc_key", ["create_task", "create_task_scheduled"])
def test_create_task_content_field_discourages_completion_reports(language: str, desc_key: str):
    from openjiuwen.agent_teams.tools.locales import make_translator

    desc = make_translator(language)(desc_key)

    if language == "cn":
        assert "**不要**写\"完成后用 send_message 向 leader 汇报" in desc
    else:
        assert "Do **not** ask the member to `send_message` you a report" in desc


@pytest.mark.level1
@pytest.mark.parametrize("language", ["cn", "en"])
@pytest.mark.parametrize("desc_key", ["create_task", "create_task_scheduled"])
def test_create_task_tells_the_leader_upstream_summaries_arrive_on_their_own(language: str, desc_key: str):
    from openjiuwen.agent_teams.tools.locales import make_translator

    desc = make_translator(language)(desc_key)

    if language == "cn":
        assert "**无需**写\"开工前用 view_task 读取前序任务的 note\"" in desc
    else:
        assert "**no need** to write \"before starting, read the upstream tasks' notes with view_task\"" in desc


@pytest.mark.level1
@pytest.mark.parametrize("key", ["dispatcher.all_done_persistent", "scheduler.leader_all_done"])
def test_all_done_prompts_discourage_wrap_up_broadcasts(key: str):
    from openjiuwen.agent_teams.i18n import get_language, set_language, t

    previous = get_language()
    try:
        set_language("cn")
        assert "无需向成员发送收尾或待命通知" in t(key, count=1)
        set_language("en")
        assert "Do not send members a wrap-up or stand-by notice" in t(key, count=1)
    finally:
        set_language(previous)


@pytest.mark.level1
@pytest.mark.parametrize("language", ["cn", "en"])
@pytest.mark.parametrize(("desc_key", "param"), [("claim_task", "summary"), ("member_complete_task", "summary")])
def test_summary_param_asks_for_short_deliverables_inline(language: str, desc_key: str, param: str):
    from openjiuwen.agent_teams.tools.locales import make_translator

    text = make_translator(language)(desc_key, param)

    if language == "cn":
        assert "交付物本身很短" in text
    else:
        assert "when the deliverable itself is short" in text
