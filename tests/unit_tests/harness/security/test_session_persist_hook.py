# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session vs User persist hooks on PermissionInterruptRail."""

from __future__ import annotations

import pytest

from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost


def test_session_remember_calls_session_persist_hook() -> None:
    user_calls: list[dict] = []
    session_calls: list[dict] = []

    host = ToolPermissionHost(
        persist_allow_rule=lambda cfg: user_calls.append(cfg) or True,
        persist_session_allow_rule=lambda cfg: session_calls.append(cfg) or True,
        get_permissions_snapshot=lambda: {
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
            "approval_overrides": [],
        },
    )
    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
        },
        host=host,
    )
    ok = rail._persist_session_allow("bash", {"command": "git status"})
    assert ok is True
    assert session_calls
    assert user_calls == []
    overrides = session_calls[0].get("approval_overrides") or []
    assert any(
        isinstance(o, dict) and o.get("action") == "allow" for o in overrides
    )


def test_permanent_remember_calls_user_persist_hook() -> None:
    user_calls: list[dict] = []
    session_calls: list[dict] = []
    host = ToolPermissionHost(
        persist_allow_rule=lambda cfg: user_calls.append(cfg) or True,
        persist_session_allow_rule=lambda cfg: session_calls.append(cfg) or True,
        get_permissions_snapshot=lambda: {
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
            "approval_overrides": [],
        },
    )
    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
        },
        host=host,
    )
    ok = rail._persist_allow_always("bash", {"command": "git status"})
    assert ok is True
    assert user_calls
    assert session_calls == []


def test_omitted_persist_allow_with_auto_confirm_is_permanent() -> None:
    from openjiuwen.harness.security.models import PermissionConfirmResponse

    parsed = PermissionInterruptRail.parse_confirm_payload(
        {"approved": True, "auto_confirm": True}
    )
    assert isinstance(parsed, PermissionConfirmResponse)
    assert parsed.persist_allow is None
    assert parsed.wants_permanent_persist() is True
    assert parsed.wants_session_persist() is False

    acp = PermissionConfirmResponse(approved=True, auto_confirm=True)
    assert acp.persist_allow is None
    assert acp.wants_permanent_persist() is True


def test_explicit_persist_allow_false_is_session() -> None:
    parsed = PermissionInterruptRail.parse_confirm_payload(
        {"approved": True, "auto_confirm": True, "persist_allow": False}
    )
    assert parsed is not None
    assert parsed.persist_allow is False
    assert parsed.wants_permanent_persist() is False
    assert parsed.wants_session_persist() is True


def test_explicit_persist_allow_true_is_permanent() -> None:
    parsed = PermissionInterruptRail.parse_confirm_payload(
        {"approved": True, "auto_confirm": True, "persist_allow": True}
    )
    assert parsed is not None
    assert parsed.wants_permanent_persist() is True
    assert parsed.wants_session_persist() is False


def test_should_store_auto_confirm_after_session_persist_success() -> None:
    """Session overlay persist must still store auto-confirm.

    ``first_check`` reloads the disk snapshot and would otherwise drop
    in-memory session rules. Auto-confirm is the rail-side safety net.
    """
    assert PermissionInterruptRail._should_store_auto_confirm(
        approved=True,
        auto_confirm=True,
        session=object(),
        auto_confirm_key="bash:python test.py",
        persisted=True,
        permanent=False,
    ) is True


def test_should_not_store_auto_confirm_after_permanent_persist_success() -> None:
    """Permanent disk persist is reloaded from snapshot; skip auto-confirm."""
    assert PermissionInterruptRail._should_store_auto_confirm(
        approved=True,
        auto_confirm=True,
        session=object(),
        auto_confirm_key="bash:python test.py",
        persisted=True,
        permanent=True,
    ) is False


def _session_store() -> tuple[object, dict]:
    stored: dict = {}

    class _Session:
        def get_state(self, key):
            return stored.get(key)

        def update_state(self, state: dict) -> None:
            stored.update(state)

    return _Session(), stored


def _ask_bash_rail(*, persist_session=None, persist_permanent=None):
    disk_snapshot = {
        "enabled": True,
        "tools": {"bash": "ask"},
        "defaults": {"*": "allow"},
        "rules": [],
        "approval_overrides": [],
    }
    host = ToolPermissionHost(
        persist_allow_rule=persist_permanent,
        persist_session_allow_rule=persist_session,
        get_permissions_snapshot=lambda: dict(disk_snapshot),
    )
    rail = PermissionInterruptRail(config=dict(disk_snapshot), host=host)
    return rail, disk_snapshot


@pytest.mark.asyncio
async def test_session_remember_stores_auto_confirm_when_session_persist_succeeds() -> None:
    """Web resume path: persist_allow=False + persist hook True still stores the key."""
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
    from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult

    session_calls: list[dict] = []
    rail, _disk = _ask_bash_rail(
        persist_session=lambda cfg: session_calls.append(cfg) or True,
    )
    session, stored = _session_store()
    ctx = AgentCallbackContext(agent=object(), session=session)
    tool_call = ToolCall(
        id="c1",
        type="function",
        name="bash",
        arguments='{"command": "python test.py"}',
    )
    auto_confirm_key = rail._get_auto_confirm_key(tool_call)

    decision = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=tool_call,
        user_input={"approved": True, "auto_confirm": True, "persist_allow": False},
    )
    assert isinstance(decision, ApproveResult)
    assert session_calls
    auto = stored.get(INTERRUPT_AUTO_CONFIRM_KEY) or {}
    assert auto.get(auto_confirm_key) is True

    # Disk snapshot has no overlay: engine memory is wiped, auto-confirm must hit.
    decision2 = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=tool_call,
        user_input=None,
        auto_confirm_config=stored.get(INTERRUPT_AUTO_CONFIRM_KEY),
    )
    assert isinstance(decision2, ApproveResult)


@pytest.mark.asyncio
async def test_session_remember_without_hook_still_stores_auto_confirm() -> None:
    """No persist_session_allow_rule: memory-only persist returns True, still store."""
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
    from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult

    rail, _disk = _ask_bash_rail()
    session, stored = _session_store()
    ctx = AgentCallbackContext(agent=object(), session=session)
    tool_call = ToolCall(
        id="c1",
        type="function",
        name="bash",
        arguments='{"command": "python test.py"}',
    )
    auto_confirm_key = rail._get_auto_confirm_key(tool_call)

    decision = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=tool_call,
        user_input={"approved": True, "auto_confirm": True, "persist_allow": False},
    )
    assert isinstance(decision, ApproveResult)
    auto = stored.get(INTERRUPT_AUTO_CONFIRM_KEY) or {}
    assert auto.get(auto_confirm_key) is True


@pytest.mark.asyncio
async def test_permanent_remember_does_not_store_auto_confirm_when_persist_succeeds() -> None:
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
    from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult

    user_calls: list[dict] = []
    rail, _disk = _ask_bash_rail(
        persist_permanent=lambda cfg: user_calls.append(cfg) or True,
    )
    session, stored = _session_store()
    ctx = AgentCallbackContext(agent=object(), session=session)
    tool_call = ToolCall(
        id="c1",
        type="function",
        name="bash",
        arguments='{"command": "python test.py"}',
    )

    decision = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=tool_call,
        user_input={"approved": True, "auto_confirm": True, "persist_allow": True},
    )
    assert isinstance(decision, ApproveResult)
    assert user_calls
    auto = stored.get(INTERRUPT_AUTO_CONFIRM_KEY) or {}
    assert auto.get(rail._get_auto_confirm_key(tool_call)) is not True


class _SessionWithId:
    def __init__(self, session_id: str, store: dict | None = None) -> None:
        self._session_id = session_id
        self._store = store if store is not None else {}

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key):
        return self._store.get(key)

    def update_state(self, state: dict) -> None:
        self._store.update(state)


def test_session_persist_hook_receives_session_id_from_ctx() -> None:
    """Interrupt resume often has an empty ContextVar; rail must pass ctx.session id."""
    received: list[tuple[dict, str | None]] = []

    def persist(cfg: dict, session_id: str | None = None) -> bool:
        received.append((cfg, session_id))
        return True

    host = ToolPermissionHost(
        persist_session_allow_rule=persist,
        get_permissions_snapshot=lambda: {
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
            "approval_overrides": [],
        },
    )
    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
        },
        host=host,
    )
    ok = rail._persist_session_allow(
        "bash",
        {"command": "python test.py"},
        session_id="sess-from-ctx",
    )
    assert ok is True
    assert received
    assert received[0][1] == "sess-from-ctx"


def test_session_persist_hook_without_session_id_kwarg_still_works() -> None:
    """Existing one-arg host hooks must keep working."""
    session_calls: list[dict] = []
    host = ToolPermissionHost(
        persist_session_allow_rule=lambda cfg: session_calls.append(cfg) or True,
        get_permissions_snapshot=lambda: {
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
            "approval_overrides": [],
        },
    )
    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
        },
        host=host,
    )
    ok = rail._persist_session_allow(
        "bash",
        {"command": "git status"},
        session_id="sess-ignored-by-hook",
    )
    assert ok is True
    assert session_calls


@pytest.mark.asyncio
async def test_session_remember_forwards_ctx_session_id_to_persist_hook() -> None:
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
    from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult

    received: list[str | None] = []

    def persist(cfg: dict, session_id: str | None = None) -> bool:
        received.append(session_id)
        return True

    rail, _disk = _ask_bash_rail(persist_session=persist)
    session = _SessionWithId("chat-sess-42")
    ctx = AgentCallbackContext(agent=object(), session=session)
    tool_call = ToolCall(
        id="c1",
        type="function",
        name="bash",
        arguments='{"command": "python test.py"}',
    )
    decision = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=tool_call,
        user_input={"approved": True, "auto_confirm": True, "persist_allow": False},
    )
    assert isinstance(decision, ApproveResult)
    assert received == ["chat-sess-42"]


@pytest.mark.asyncio
async def test_first_check_snapshot_forwards_ctx_session_id() -> None:
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

    seen: list[str | None] = []
    overlay = {
        "enabled": True,
        "tools": {"bash": "ask"},
        "defaults": {"*": "allow"},
        "rules": [],
        "approval_overrides": [
            {
                "id": "sess_ov",
                "tools": ["bash"],
                "match_type": "command",
                "pattern": "python test.py",
                "action": "allow",
            }
        ],
    }

    def snapshot(session_id: str | None = None) -> dict:
        seen.append(session_id)
        return dict(overlay)

    host = ToolPermissionHost(get_permissions_snapshot=snapshot)
    rail = PermissionInterruptRail(config={"enabled": True, "tools": {"bash": "ask"}}, host=host)
    ctx = AgentCallbackContext(agent=object(), session=_SessionWithId("chat-sess-42"))
    tool_call = ToolCall(
        id="c1",
        type="function",
        name="bash",
        arguments='{"command": "python test.py"}',
    )
    await rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input=None)
    assert seen == ["chat-sess-42"]


def test_builtin_deny_does_not_persist_session_allow() -> None:
    from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import inline_package_command_rules

    session_calls: list[dict] = []
    effective = inline_package_command_rules(
        {
            "enabled": True,
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
            "rules": [],
        }
    )
    host = ToolPermissionHost(
        persist_session_allow_rule=lambda cfg: session_calls.append(cfg) or True,
        get_permissions_snapshot=lambda: effective,
    )
    rail = PermissionInterruptRail(config=effective, host=host)
    ok = rail._persist_session_allow("bash", {"command": "shutdown -h now"})
    assert ok is False
    assert session_calls == []
