# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.session.internal.agent_team import AgentTeamSession


def test_agent_team_session_group_id_aliases_team_id():
    session = AgentTeamSession(session_id="s1", team_id="team-42")
    assert session.team_id() == "team-42"
    assert session.group_id() == "team-42"
    assert session.session_id() == "s1"
