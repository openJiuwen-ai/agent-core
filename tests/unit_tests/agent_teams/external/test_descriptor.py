# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the external-agent join descriptor."""

import pytest

from openjiuwen.agent_teams.external import TEAM_JOIN_ENV, TeamJoinDescriptor
from openjiuwen.agent_teams.tools.database.config import DatabaseConfig, DatabaseType
from openjiuwen.core.common.exception.errors import BaseError


@pytest.mark.level0
def test_descriptor_json_roundtrip_preserves_fields():
    descriptor = TeamJoinDescriptor(
        session_id="s1",
        team_name="t1",
        member_name="dev-1",
        role="leader",
        language="en",
        db_config=DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string="/tmp/team.db"),
    )

    restored = TeamJoinDescriptor.from_json(descriptor.to_json())

    assert restored.session_id == "s1"
    assert restored.team_name == "t1"
    assert restored.member_name == "dev-1"
    assert restored.role == "leader"
    assert restored.language == "en"
    assert restored.db_config.connection_string == "/tmp/team.db"


@pytest.mark.level0
def test_descriptor_env_roundtrip():
    descriptor = TeamJoinDescriptor(session_id="s", team_name="t", member_name="m")

    env = descriptor.to_env()
    assert TEAM_JOIN_ENV in env

    restored = TeamJoinDescriptor.from_env(env)
    assert restored.member_name == "m"
    assert restored.role == "teammate"


@pytest.mark.level0
def test_descriptor_from_env_missing_var_raises():
    with pytest.raises(BaseError):
        TeamJoinDescriptor.from_env({})


@pytest.mark.level0
def test_descriptor_from_json_malformed_raises():
    with pytest.raises(BaseError):
        TeamJoinDescriptor.from_json("{ not valid json")
