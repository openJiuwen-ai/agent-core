# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pin ``TeamPermissionRail.parse_confirm_payload`` ``decided_by`` contract.

The ``:138-147`` branch preserves ``decided_by`` when the caller passes a
``TeamPermissionConfirmResponse`` that already carries it; ``decided_by=None``
falls through to the ``:140-145`` default of ``"leader"`` (existing behavior).

These tests pin that contract regardless of how the user-mediated resume
flow is ultimately wired (see task-6-report.md for the investigation).
"""

from openjiuwen.agent_teams.rails.confirm_payload import (
    TeamPermissionConfirmResponse,
)
from openjiuwen.agent_teams.rails.team_permission_rail import TeamPermissionRail


def test_parse_confirm_preserves_decided_by_user() -> None:
    """:138-147 branch preserves decided_by on TeamPermissionConfirmResponse.

    A user-mediated caller that passes ``decided_by="user"`` must see it
    pass through unchanged (the spec (a') preserve contract).
    """
    resp = TeamPermissionConfirmResponse(
        approved=True, feedback="ok", auto_confirm=False, decided_by="user"
    )
    out = TeamPermissionRail.parse_confirm_payload(resp)
    assert out is not None
    assert out.decided_by == "user"


def test_parse_confirm_default_leader_when_decided_by_none() -> None:
    """``decided_by is None`` -> :140-145 fills ``"leader"`` (existing behavior)."""
    resp = TeamPermissionConfirmResponse(
        approved=True, feedback=None, auto_confirm=False, decided_by=None
    )
    out = TeamPermissionRail.parse_confirm_payload(resp)
    assert out is not None
    assert out.decided_by == "leader"
