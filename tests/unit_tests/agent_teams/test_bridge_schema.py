# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for Bridge Agent schema layer.

Covers:
- ``BridgeMailboxInjectMode`` / ``TeamRole.BRIDGE_AGENT`` enums.
- ``BridgeMemberSpec`` field defaults and ``role_type`` lock-in.
- ``TeamAgentSpec.predefined_members`` discriminated-union dispatch
  (``BridgeMemberSpec`` vs base ``TeamMemberSpec``) on round-trip.
- ``_validate_bridge_consistency`` ceiling enforcement.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.schema.blueprint import (
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import (
    BridgeMailboxInjectMode,
    BridgeMemberSpec,
    TeamMemberSpec,
    TeamRole,
)

# ---------------------------------------------------------------------------
# Enum surface
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_bridge_role_enum_value():
    assert TeamRole.BRIDGE_AGENT.value == "bridge_agent"


@pytest.mark.level0
def test_mailbox_inject_mode_values():
    assert BridgeMailboxInjectMode.PASSTHROUGH.value == "passthrough"
    assert BridgeMailboxInjectMode.REPHRASE.value == "rephrase"


# ---------------------------------------------------------------------------
# BridgeMemberSpec defaults and field lock
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_bridge_member_spec_defaults():
    spec = BridgeMemberSpec(
        member_name="codex",
        display_name="Codex Bridge",
        persona="Senior python reviewer",
    )
    assert spec.role_type == TeamRole.BRIDGE_AGENT
    assert spec.mailbox_inject_mode == BridgeMailboxInjectMode.PASSTHROUGH
    assert spec.protocol == ""
    assert spec.adapter_config == {}


@pytest.mark.level0
def test_bridge_member_spec_role_type_locked():
    """BridgeMemberSpec.role_type is Literal[BRIDGE_AGENT] — assigning
    a different role at construction must raise."""
    with pytest.raises(Exception):
        BridgeMemberSpec(
            member_name="codex",
            display_name="Codex",
            persona="x",
            role_type=TeamRole.TEAMMATE,  # type: ignore[arg-type]
        )


@pytest.mark.level0
def test_base_team_member_spec_rejects_bridge_role():
    """The base TeamMemberSpec.role_type Literal excludes BRIDGE_AGENT
    so users can't accidentally smuggle bridge config in without using
    the dedicated subclass."""
    with pytest.raises(Exception):
        TeamMemberSpec(
            member_name="codex",
            display_name="Codex",
            persona="x",
            role_type=TeamRole.BRIDGE_AGENT,  # type: ignore[arg-type]
        )


@pytest.mark.level0
def test_bridge_member_spec_custom_fields():
    spec = BridgeMemberSpec(
        member_name="claudecode",
        display_name="Claude Code Bridge",
        persona="Pair-programmer",
        mailbox_inject_mode=BridgeMailboxInjectMode.REPHRASE,
        protocol="claudecode",
        adapter_config={"endpoint": "stdio://claude-code", "relay_timeout_s": 60},
    )
    assert spec.mailbox_inject_mode == BridgeMailboxInjectMode.REPHRASE
    assert spec.protocol == "claudecode"
    assert spec.adapter_config["relay_timeout_s"] == 60


# ---------------------------------------------------------------------------
# Discriminated union round-trip
# ---------------------------------------------------------------------------


def _spec_with_predefined(members) -> TeamAgentSpec:
    return TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="bridge_team",
        enable_bridge=True,
        predefined_members=members,
    )


@pytest.mark.level0
def test_discriminator_dispatches_bridge_subclass():
    """When predefined_members contains a bridge entry, the validated
    instance is a ``BridgeMemberSpec`` (not the base TeamMemberSpec)."""
    spec = _spec_with_predefined(
        [
            BridgeMemberSpec(
                member_name="codex",
                display_name="Codex",
                persona="x",
                protocol="codex",
            ),
        ],
    )
    member = spec.predefined_members[0]
    assert isinstance(member, BridgeMemberSpec)
    assert member.protocol == "codex"


@pytest.mark.level0
def test_discriminator_keeps_base_for_teammate():
    """Non-bridge entries continue to materialize as the base TeamMemberSpec."""
    spec = _spec_with_predefined(
        [
            TeamMemberSpec(
                member_name="alice",
                display_name="Alice",
                persona="x",
                role_type=TeamRole.TEAMMATE,
            ),
        ],
    )
    member = spec.predefined_members[0]
    assert type(member) is TeamMemberSpec
    assert member.role_type == TeamRole.TEAMMATE


@pytest.mark.level0
def test_discriminator_mixed_predefined():
    """Bridge + teammate + human entries materialize as their respective classes."""
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="bridge_team",
        enable_bridge=True,
        enable_hitt=True,
        predefined_members=[
            BridgeMemberSpec(
                member_name="codex",
                display_name="Codex",
                persona="x",
            ),
            TeamMemberSpec(
                member_name="alice",
                display_name="Alice",
                persona="y",
                role_type=TeamRole.TEAMMATE,
            ),
            TeamMemberSpec(
                member_name="bob_human",
                display_name="Bob",
                persona="z",
                role_type=TeamRole.HUMAN_AGENT,
            ),
        ],
    )
    members = spec.predefined_members
    assert isinstance(members[0], BridgeMemberSpec)
    assert type(members[1]) is TeamMemberSpec
    assert members[1].role_type == TeamRole.TEAMMATE
    assert type(members[2]) is TeamMemberSpec
    assert members[2].role_type == TeamRole.HUMAN_AGENT


@pytest.mark.level0
def test_discriminator_round_trip_via_dict():
    """A bridge member declared as a raw dict (e.g. from YAML / JSON) is
    dispatched to the BridgeMemberSpec subclass by the discriminator."""
    spec = TeamAgentSpec.model_validate(
        {
            "agents": {"leader": {}},
            "team_name": "bridge_team",
            "enable_bridge": True,
            "predefined_members": [
                {
                    "member_name": "codex",
                    "display_name": "Codex",
                    "persona": "x",
                    "role_type": "bridge_agent",
                    "mailbox_inject_mode": "rephrase",
                    "protocol": "codex",
                    "adapter_config": {"endpoint": "stdio://codex"},
                },
                {
                    "member_name": "alice",
                    "display_name": "Alice",
                    "persona": "y",
                    "role_type": "teammate",
                },
            ],
        },
    )
    bridge = spec.predefined_members[0]
    assert isinstance(bridge, BridgeMemberSpec)
    assert bridge.mailbox_inject_mode == BridgeMailboxInjectMode.REPHRASE
    assert bridge.adapter_config == {"endpoint": "stdio://codex"}

    teammate = spec.predefined_members[1]
    assert type(teammate) is TeamMemberSpec


@pytest.mark.level0
def test_legacy_dump_without_bridge_fields_still_loads():
    """Pre-existing spec dumps (no bridge_* fields) must round-trip
    cleanly — backwards compatibility for stored team configs."""
    dumped = {
        "agents": {"leader": {}},
        "team_name": "legacy",
        "predefined_members": [
            {
                "member_name": "alice",
                "display_name": "Alice",
                "persona": "x",
                "role_type": "teammate",
            },
        ],
    }
    spec = TeamAgentSpec.model_validate(dumped)
    assert spec.enable_bridge is False
    assert type(spec.predefined_members[0]) is TeamMemberSpec


# ---------------------------------------------------------------------------
# enable_bridge consistency
# ---------------------------------------------------------------------------


def _minimal_spec(**overrides) -> TeamAgentSpec:
    agents = {"leader": DeepAgentSpec()}
    base: dict = {"agents": agents, "team_name": "bridge_team"}
    base.update(overrides)
    return TeamAgentSpec(**base)


@pytest.mark.level0
def test_enable_bridge_true_with_predefined_passes():
    bridge = BridgeMemberSpec(
        member_name="codex",
        display_name="Codex",
        persona="x",
    )
    spec = _minimal_spec(enable_bridge=True, predefined_members=[bridge])
    spec._validate_bridge_consistency()  # must not raise


@pytest.mark.level0
def test_enable_bridge_true_without_predefined_passes():
    """Dynamic spawn path is allowed when ceiling is open without
    predefined bridge entries."""
    spec = _minimal_spec(enable_bridge=True)
    spec._validate_bridge_consistency()  # must not raise


@pytest.mark.level0
def test_enable_bridge_false_with_predefined_raises():
    bridge = BridgeMemberSpec(
        member_name="codex",
        display_name="Codex",
        persona="x",
    )
    spec = _minimal_spec(enable_bridge=False, predefined_members=[bridge])
    from openjiuwen.core.common.exception.errors import BaseError

    with pytest.raises(BaseError, match="enable_bridge=False"):
        spec._validate_bridge_consistency()


@pytest.mark.level0
def test_enable_bridge_false_no_predefined_passes():
    spec = _minimal_spec(enable_bridge=False)
    spec._validate_bridge_consistency()  # must not raise


@pytest.mark.level0
def test_enable_bridge_default_is_false():
    spec = _minimal_spec()
    assert spec.enable_bridge is False
