# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A sub-agent is gated by the same tool permission policy as its parent.

``DeepAgent._queue_pending_rails`` builds the PermissionInterruptRail from
``config.permissions`` and ``config.permission_host``. ``create_subagent``
assembles the sub-agent's config itself, so unless it forwards those two the
sub-agent's config holds only what the spec's own ``factory_kwargs`` set. For a
spec that sets neither -- which is every spec that does not know to -- no rail
is built and its tool calls run ungated however strict the parent's policy is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.security.host import (
    PermissionConfirmationRequest,
    ToolPermissionHost,
)
from openjiuwen.harness.security.models import PermissionConfirmResponse

pytestmark = pytest.mark.level0

CODE_AGENT_FACTORY_NAME = "code_agent"


def _fake_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="fake-key-for-subagent-permissions",
            api_base="http://localhost:0",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="fake-subagent-permissions"),
    )


def _enabled_permissions() -> dict[str, Any]:
    return {"enabled": True, "tools": {"read_file": "ask", "bash": "ask"}}


def _plain_spec(name: str = "worker") -> SubAgentConfig:
    """A spec with no factory_name, so create_subagent uses create_deep_agent."""
    return SubAgentConfig(
        agent_card=AgentCard(name=name, description=f"{name} under test"),
        system_prompt="Do the delegated work.",
    )


def _build_parent(tmp_path: Path, **config_kwargs: Any):
    return create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[_plain_spec()],
        language="en",
        **config_kwargs,
    )


def _permission_rails(agent: Any) -> list[PermissionInterruptRail]:
    """Return the permission rails queued on ``agent``."""
    return [r for r in agent._pending_rails if isinstance(r, PermissionInterruptRail)]


def _only_permission_rail(agent: Any) -> PermissionInterruptRail:
    """The single permission rail on ``agent``, asserted rather than indexed."""
    rails = _permission_rails(agent)
    assert len(rails) == 1, f"expected one permission rail, got {len(rails)}"
    return rails[0]


def test_subagent_mounts_permission_rail_when_parent_has_one(tmp_path) -> None:
    """The gate the parent runs behind also applies to work it delegates."""
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[_plain_spec()],
        language="en",
        permissions=_enabled_permissions(),
    )
    assert _permission_rails(parent), "parent should mount a rail for this policy"

    sub = parent.create_subagent("worker", "sub_session_id")

    assert _permission_rails(sub), "sub-agent mounted no permission rail"


def test_subagent_permission_policy_matches_the_parent(tmp_path) -> None:
    """The sub-agent is gated by the same rules, not by weaker defaults."""
    permissions = _enabled_permissions()
    parent = _build_parent(tmp_path, permissions=permissions)

    sub = parent.create_subagent("worker", "sub_session_id")

    assert sub._deep_config.permissions == permissions
    rail = _only_permission_rail(sub)
    assert rail._static_config["tools"] == permissions["tools"]


def test_subagent_mounts_no_permission_rail_when_parent_has_none(tmp_path) -> None:
    """No policy anywhere means no rail anywhere; nothing is invented."""
    parent = _build_parent(tmp_path)
    assert parent._deep_config.permissions is None

    sub = parent.create_subagent("worker", "sub_session_id")

    assert sub._deep_config.permissions is None
    assert _permission_rails(sub) == []


@pytest.mark.asyncio
async def test_subagent_rail_reaches_the_host_confirmation_callback(tmp_path) -> None:
    """The callback that puts a permission question in front of a user.

    Without the host the sub-agent still has a way to ask: the rail falls
    through to the built-in ``ConfirmInterruptRail`` interrupt. What it loses is
    the channel the product answers permission questions on, so the question is
    raised over a protocol the host is not listening to.
    ``build_permission_interrupt_rail`` may rebuild the host to fill in
    ``resolve_workspace_dir``, so identity is asserted on the callback rather
    than on the host object.
    """
    asked: list[str] = []

    async def _confirm(request: PermissionConfirmationRequest) -> PermissionConfirmResponse:
        asked.append(request.auto_confirm_key)
        return PermissionConfirmResponse(approved=True)

    host = ToolPermissionHost(request_permission_confirmation=_confirm)
    parent = _build_parent(tmp_path, permissions=_enabled_permissions(), permission_host=host)

    sub = parent.create_subagent("worker", "sub_session_id")

    rail = _only_permission_rail(sub)
    assert rail._host.request_permission_confirmation is _confirm

    response = await rail._host.request_permission_confirmation(
        PermissionConfirmationRequest(
            ctx=None,
            tool_call=None,
            result=None,
            auto_confirm_key="read_file",
        )
    )
    assert response.approved is True
    assert asked == ["read_file"], "the sub-agent's question never reached the host"


def test_subagent_shares_the_parent_host_object(tmp_path) -> None:
    """One host instance serves both agents; it holds no per-agent state."""
    host = ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path)
    parent = _build_parent(tmp_path, permissions=_enabled_permissions(), permission_host=host)

    sub = parent.create_subagent("worker", "sub_session_id")

    assert sub._deep_config.permission_host is host
    assert _only_permission_rail(sub)._host is host


def test_subagent_cannot_mutate_the_parent_policy_through_its_rail(tmp_path) -> None:
    """The rail deep-copies the policy, so the two agents cannot corrupt each other."""
    permissions = _enabled_permissions()
    parent = _build_parent(tmp_path, permissions=permissions)

    sub = parent.create_subagent("worker", "sub_session_id")

    rail = _only_permission_rail(sub)
    rail._static_config["tools"]["bash"] = "allow"

    assert permissions["tools"]["bash"] == "ask"
    assert parent._deep_config.permissions["tools"]["bash"] == "ask"


# --- permissions off: behaviour must be identical to before ----------------


def test_permissions_disabled_mounts_no_rail_on_the_subagent(tmp_path) -> None:
    """``enabled: false`` is the deployed setting; it must stay inert."""
    parent = _build_parent(tmp_path, permissions={"enabled": False, "tools": {"bash": "ask"}})
    assert _permission_rails(parent) == []

    sub = parent.create_subagent("worker", "sub_session_id")

    assert _permission_rails(sub) == []


def test_permissions_disabled_leaves_the_subagent_rail_set_unchanged(tmp_path) -> None:
    """The rails a sub-agent gets with permissions off are the ones it got before.

    Compared against a parent holding no permissions key at all, which is the
    pre-change shape of every sub-agent config.
    """
    off = _build_parent(tmp_path, permissions={"enabled": False})
    absent = _build_parent(tmp_path)

    sub_off = off.create_subagent("worker", "sub_session_id")
    sub_absent = absent.create_subagent("worker", "sub_session_id")

    assert [type(r) for r in sub_off._pending_rails] == [
        type(r) for r in sub_absent._pending_rails
    ]


def test_permissions_absent_forwards_none_to_the_subagent_factory(tmp_path) -> None:
    """Nothing new is handed to a factory when the parent holds no policy."""
    spec = SubAgentConfig(
        agent_card=AgentCard(name="reviewer", description="reviewer"),
        system_prompt="Review strictly.",
        factory_name=CODE_AGENT_FACTORY_NAME,
    )
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[spec],
        language="en",
    )
    factory_result = object()

    with patch(
        "openjiuwen.harness.subagents.code_agent.create_code_agent",
        return_value=factory_result,
    ) as mock_factory:
        assert parent.create_subagent("reviewer", "sub_session_id") is factory_result

    call_kwargs = mock_factory.call_args.kwargs
    assert call_kwargs["permissions"] is None
    assert call_kwargs["permission_host"] is None


# --- uniform across every sub-agent type ------------------------------------


@pytest.mark.parametrize(
    ("factory_name", "patch_target"),
    [
        ("code_agent", "openjiuwen.harness.subagents.code_agent.create_code_agent"),
        (
            "research_agent",
            "openjiuwen.harness.subagents.research_agent.create_research_agent",
        ),
        (
            "browser_agent",
            "openjiuwen.harness.subagents.browser_agent.create_browser_agent",
        ),
        (
            "mobile_gui_agent",
            "openjiuwen.harness.subagents.mobile_gui_agent.create_mobile_gui_agent",
        ),
    ],
)
def test_every_subagent_factory_receives_the_parent_permissions(
    tmp_path, factory_name: str, patch_target: str
) -> None:
    """Every factory forwards ``**config_kwargs`` into ``create_deep_agent``."""
    permissions = _enabled_permissions()
    host = ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path)
    spec = SubAgentConfig(
        agent_card=AgentCard(name="worker", description="worker"),
        system_prompt="Do the delegated work.",
        factory_name=factory_name,
    )
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[spec],
        language="en",
        permissions=permissions,
        permission_host=host,
    )
    factory_result = object()

    with patch(patch_target, return_value=factory_result) as mock_factory:
        assert parent.create_subagent("worker", "sub_session_id") is factory_result

    call_kwargs = mock_factory.call_args.kwargs
    assert call_kwargs["permissions"] is permissions
    assert call_kwargs["permission_host"] is host


# --- collision with the operator's own factory_kwargs -----------------------
#
# ``create_subagent`` splats ``create_kwargs`` and ``spec.factory_kwargs`` into
# one call, and ``create_deep_agent`` takes both keys through ``**config_kwargs``
# rather than as named parameters. An operator could therefore already set
# either of them in ``factory_kwargs``, and did so to configure a sub-agent's
# policy at all before the parent's was inherited. Naming a key in both dicts is
# a duplicate keyword argument, not an override, so it must not reach the call.


def _operator_permissions() -> dict[str, Any]:
    """A policy an operator wrote for one sub-agent, distinct from the parent's."""
    return {"enabled": True, "tools": {"bash": "deny"}}


def test_factory_kwargs_permissions_does_not_raise_on_the_inherited_key(tmp_path) -> None:
    """The operator's own ``permissions`` entry must still construct."""
    spec = SubAgentConfig(
        agent_card=AgentCard(name="worker", description="worker"),
        system_prompt="Do the delegated work.",
        factory_kwargs={"permissions": _operator_permissions()},
    )
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[spec],
        language="en",
        permissions=_enabled_permissions(),
    )

    sub = parent.create_subagent("worker", "sub_session_id")

    assert sub._deep_config.permissions == _operator_permissions()


def test_factory_kwargs_permissions_wins_when_the_parent_holds_no_policy(tmp_path) -> None:
    """The key is present in ``create_kwargs`` whether or not a policy exists.

    A parent with no policy forwards ``permissions=None``, which collides just
    as a real policy would, so this path must be pinned separately.
    """
    spec = SubAgentConfig(
        agent_card=AgentCard(name="worker", description="worker"),
        system_prompt="Do the delegated work.",
        factory_kwargs={"permissions": _operator_permissions()},
    )
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[spec],
        language="en",
    )
    assert parent._deep_config.permissions is None

    sub = parent.create_subagent("worker", "sub_session_id")

    assert sub._deep_config.permissions == _operator_permissions()
    assert _permission_rails(sub), "the operator's policy should still mount a rail"


def test_factory_kwargs_permission_host_wins_over_the_inherited_host(tmp_path) -> None:
    """``permission_host`` collides on the same terms and resolves the same way."""
    parent_host = ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path)
    operator_host = ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path)
    spec = SubAgentConfig(
        agent_card=AgentCard(name="worker", description="worker"),
        system_prompt="Do the delegated work.",
        factory_kwargs={"permission_host": operator_host},
    )
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[spec],
        language="en",
        permissions=_enabled_permissions(),
        permission_host=parent_host,
    )

    sub = parent.create_subagent("worker", "sub_session_id")

    assert sub._deep_config.permission_host is operator_host


def test_factory_kwargs_permissions_alone_still_inherits_the_parent_host(tmp_path) -> None:
    """The two keys are dropped independently, so a partial override inherits the rest."""
    parent_host = ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path)
    spec = SubAgentConfig(
        agent_card=AgentCard(name="worker", description="worker"),
        system_prompt="Do the delegated work.",
        factory_kwargs={"permissions": _operator_permissions()},
    )
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[spec],
        language="en",
        permissions=_enabled_permissions(),
        permission_host=parent_host,
    )

    sub = parent.create_subagent("worker", "sub_session_id")

    assert sub._deep_config.permissions == _operator_permissions()
    assert sub._deep_config.permission_host is parent_host


@pytest.mark.parametrize(
    ("factory_name", "patch_target"),
    [
        ("code_agent", "openjiuwen.harness.subagents.code_agent.create_code_agent"),
        (
            "research_agent",
            "openjiuwen.harness.subagents.research_agent.create_research_agent",
        ),
        (
            "browser_agent",
            "openjiuwen.harness.subagents.browser_agent.create_browser_agent",
        ),
        (
            "mobile_gui_agent",
            "openjiuwen.harness.subagents.mobile_gui_agent.create_mobile_gui_agent",
        ),
    ],
)
def test_factory_kwargs_permissions_does_not_collide_on_any_factory(
    tmp_path, factory_name: str, patch_target: str
) -> None:
    """Every factory branch splats the same two dicts, including the browser copy."""
    operator_permissions = _operator_permissions()
    spec = SubAgentConfig(
        agent_card=AgentCard(name="worker", description="worker"),
        system_prompt="Do the delegated work.",
        factory_name=factory_name,
        factory_kwargs={"permissions": operator_permissions},
    )
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[spec],
        language="en",
        permissions=_enabled_permissions(),
    )
    factory_result = object()

    with patch(patch_target, return_value=factory_result) as mock_factory:
        assert parent.create_subagent("worker", "sub_session_id") is factory_result

    call_kwargs = mock_factory.call_args.kwargs
    assert call_kwargs["permissions"] is operator_permissions


def test_a_factory_kwargs_collision_on_another_key_still_raises(tmp_path) -> None:
    """Only these two keys are reconciled; every other collision stays loud.

    ``system_prompt`` has always been both a ``create_kwargs`` key and a named
    parameter of ``create_deep_agent``, so naming it in ``factory_kwargs`` has
    always raised. Merging the two dicts instead would have turned that, and
    every collision like it, into a silent override.
    """
    spec = SubAgentConfig(
        agent_card=AgentCard(name="worker", description="worker"),
        system_prompt="Do the delegated work.",
        factory_kwargs={"system_prompt": "an overriding prompt"},
    )
    parent = create_deep_agent(
        _fake_model(),
        card=AgentCard(name="parent", description="parent under test"),
        system_prompt="parent prompt",
        workspace=str(tmp_path / "parent_workspace"),
        subagents=[spec],
        language="en",
        permissions=_enabled_permissions(),
    )

    with pytest.raises(TypeError, match="system_prompt"):
        parent.create_subagent("worker", "sub_session_id")
