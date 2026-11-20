# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team/session topic components must not inject topic separators."""

from openjiuwen.agent_teams.schema.events import TeamTopic, swarmflow_human_reply_topic


def test_team_topic_does_not_collapse_distinct_pairs() -> None:
    first = TeamTopic.TEAM.build("s", "x:team:y")
    second = TeamTopic.TEAM.build("s:team:x", "y")

    assert first != second


def test_run_scoped_reply_topic_escapes_team_and_session() -> None:
    first = swarmflow_human_reply_topic("s", "x:team:y", "r")
    second = swarmflow_human_reply_topic("s:team:x", "y", "r")

    assert first != second


def test_plain_ids_keep_their_existing_topic_shape() -> None:
    assert TeamTopic.TEAM.build("s", "team") == "session:s:team:team:team"
