# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PermissionInterruptRail owns registration under its permission key."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from agent_shield.tool_permission import get_permission_manager

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, RejectResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.permission_key import DEFAULT_KEY

_KEY = "test-rail-manager"
_CONFIG = {
    "enabled": True,
    "schema": "tiered_policy",
    "permission_mode": "normal",
    "defaults": {"*": "ask"},
    "tools": {"bash": "allow"},
    "rules": [],
    "approval_overrides": [],
}


@pytest.fixture(autouse=True)
def _clean_key():
    yield
    manager = get_permission_manager()
    if manager.has_config(_KEY):
        manager.remove_config(_KEY)


def _call(command: str) -> ToolCall:
    return ToolCall(id="c1", type="function", name="bash", arguments=json.dumps({"command": command}))


def _ctx() -> AgentCallbackContext:
    return AgentCallbackContext(agent=MagicMock(), extra={})


def test_rail_defaults_to_default_key() -> None:
    assert PermissionInterruptRail().permission_key == DEFAULT_KEY


@pytest.mark.parametrize("bad", ["", "  ", 1])
def test_rail_rejects_invalid_key(bad) -> None:
    with pytest.raises(ValueError):
        PermissionInterruptRail(permission_key=bad)


def test_rail_registers_on_construct() -> None:
    PermissionInterruptRail(config=_CONFIG, permission_key=_KEY)
    assert get_permission_manager().has_config(_KEY)
    assert get_permission_manager().get_config(_KEY)["tools"]["bash"] == "allow"


@pytest.mark.asyncio
async def test_decides_with_config_registered_at_construct() -> None:
    rail = PermissionInterruptRail(config=_CONFIG, permission_key=_KEY)

    decision = await rail.resolve_interrupt(_ctx(), _call("ls"), None, {})

    assert isinstance(decision, ApproveResult)


@pytest.mark.asyncio
async def test_update_config_applies_to_next_decision() -> None:
    rail = PermissionInterruptRail(config=_CONFIG, permission_key=_KEY)
    deny = {**_CONFIG, "tools": {"bash": "deny"}}

    rail.update_config(deny)
    decision = await rail.resolve_interrupt(_ctx(), _call("ls"), None, {})

    assert isinstance(decision, RejectResult)


@pytest.mark.asyncio
async def test_unregistered_key_fails_closed_with_key_error() -> None:
    rail = PermissionInterruptRail(permission_key=_KEY)

    with pytest.raises(KeyError):
        await rail.resolve_interrupt(_ctx(), _call("ls"), None, {})


@pytest.mark.asyncio
async def test_inactive_checks_hook_skips_decision() -> None:
    rail = PermissionInterruptRail(
        permission_key=_KEY,
        host=ToolPermissionHost(tool_permission_checks_active=lambda: False),
    )

    decision = await rail.resolve_interrupt(_ctx(), _call("ls"), None, {})

    assert isinstance(decision, ApproveResult)


def test_uninit_releases_only_for_owner() -> None:
    rail = PermissionInterruptRail(config=_CONFIG, permission_key=_KEY)
    owner = MagicMock(name="owner")
    child = MagicMock(name="subagent")

    rail.init(owner)
    rail.init(child)  # shared inherit: second init must not steal ownership

    rail.uninit(child)
    assert get_permission_manager().has_config(_KEY)

    rail.uninit(owner)
    assert not get_permission_manager().has_config(_KEY)

    # Idempotent: a second owner uninit must not raise.
    rail.uninit(owner)


_ASK_BASH = {**_CONFIG, "tools": {"bash": "ask"}}


def test_persist_through_host_callback_requires_host_to_update_rail() -> None:
    """1B: host persists; host must call rail.update_config (rail does not)."""
    manager = get_permission_manager()
    seen: list[dict] = []
    rail_box: dict = {}

    def persist(cfg: dict) -> bool:
        seen.append(cfg)
        rail_box["rail"].update_config(cfg)
        return True

    rail = PermissionInterruptRail(
        config=_ASK_BASH,
        permission_key=_KEY,
        host=ToolPermissionHost(persist_allow_rule=persist),
    )
    rail_box["rail"] = rail

    assert rail._persist_allow_always("bash", {"command": "echo hi"}) is True
    assert seen
    assert manager.get_config(_KEY)["approval_overrides"]


def test_persist_by_rail_publishes_after_successful_yaml_write(tmp_path: Path) -> None:
    yaml_path = tmp_path / "config.yaml"
    rail = PermissionInterruptRail(
        config={**_ASK_BASH, "runtime": {"workspace_root": str(tmp_path)}},
        permission_key=_KEY,
        host=ToolPermissionHost(permission_yaml_path=yaml_path),
    )

    assert rail._persist_allow_always("bash", {"command": "echo hi"}) is True

    published = get_permission_manager().get_config(_KEY)
    assert published["approval_overrides"]
    assert published["runtime"] == {"workspace_root": str(tmp_path)}
    assert yaml_path.exists()


def test_failed_persist_publishes_nothing() -> None:
    rail = PermissionInterruptRail(
        config=_ASK_BASH,
        permission_key=_KEY,
        host=ToolPermissionHost(persist_allow_rule=lambda cfg: False),
    )

    assert rail._persist_allow_always("bash", {"command": "echo hi"}) is False
    assert get_permission_manager().get_config(_KEY) == _ASK_BASH
