# coding: utf-8
"""Role section matches DEV: member_name only (no runtime display_name inject)."""
from __future__ import annotations

from openjiuwen.agent_teams.prompts.sections import build_team_role_section
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext


def test_teammate_role_section_matches_dev_member_name_only() -> None:
    body = build_team_role_section(
        role=TeamRole.TEAMMATE,
        member_name="assistant",
        language="cn",
    ).content["cn"]

    assert "member_name: assistant" in body
    assert "自称规则" not in body
    assert "你的 display_name" not in body


def test_team_runtime_context_has_no_display_name_field() -> None:
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="office")
    assert not hasattr(ctx, "display_name") or "display_name" not in ctx.model_fields
