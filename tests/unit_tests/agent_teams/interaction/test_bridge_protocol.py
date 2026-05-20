# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the BridgeProtocolAdapter shape contract.

Covers the runtime_checkable Protocol surface and module-level
sentinel / exception types exposed by
``openjiuwen.agent_teams.interaction.bridge_protocol``.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.interaction import (
    REMOTE_UNAVAILABLE_SENTINEL,
    BridgeAgentNotEnabledError,
    BridgeProtocolAdapter,
    UnknownBridgeAgentError,
)

# ---------------------------------------------------------------------------
# Sentinel + exception classes
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_remote_unavailable_sentinel_is_stable_string():
    """The sentinel is a plain string so it can be embedded in compose
    templates and assertions without further wrapping."""
    assert isinstance(REMOTE_UNAVAILABLE_SENTINEL, str)
    assert REMOTE_UNAVAILABLE_SENTINEL  # non-empty


@pytest.mark.level0
def test_bridge_not_enabled_is_runtime_error():
    """Boundary code converts these to ``DeliverResult.failure(...)``;
    until then they propagate as a RuntimeError subclass."""
    assert issubclass(BridgeAgentNotEnabledError, RuntimeError)


@pytest.mark.level0
def test_unknown_bridge_agent_is_runtime_error():
    assert issubclass(UnknownBridgeAgentError, RuntimeError)


# ---------------------------------------------------------------------------
# Protocol structural check
# ---------------------------------------------------------------------------


class _ConformingAdapter:
    """Minimal adapter that implements every Protocol method.

    Provided in-test rather than via the public package because Phase-1
    does not ship any built-in adapter — the registry layer is left
    for follow-up.
    """

    async def connect(
        self,
        *,
        member_name: str,
        adapter_config: dict[str, object],
        bridge_persona: str,
        team_overview: str,
    ) -> None:
        """Open transport."""
        del member_name, adapter_config, bridge_persona, team_overview

    async def relay(self, *, member_name: str, text: str) -> str:
        """One text turn out, one text turn back."""
        del member_name
        return f"echo: {text}"

    async def close(self) -> None:
        """Idempotent teardown."""


class _MissingRelayAdapter:
    """Missing ``relay`` — must NOT satisfy the Protocol."""

    async def connect(
        self,
        *,
        member_name: str,
        adapter_config: dict[str, object],
        bridge_persona: str,
        team_overview: str,
    ) -> None:
        """Open transport."""
        del member_name, adapter_config, bridge_persona, team_overview

    async def close(self) -> None:
        """Idempotent teardown."""


@pytest.mark.level0
def test_conforming_adapter_passes_isinstance():
    """``BridgeProtocolAdapter`` is ``@runtime_checkable`` — a class
    that exposes all four methods satisfies ``isinstance`` without
    inheriting the Protocol."""
    assert isinstance(_ConformingAdapter(), BridgeProtocolAdapter)


@pytest.mark.level0
def test_missing_method_fails_isinstance():
    assert not isinstance(_MissingRelayAdapter(), BridgeProtocolAdapter)


@pytest.mark.level0
def test_plain_object_fails_isinstance():
    assert not isinstance(object(), BridgeProtocolAdapter)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_conforming_adapter_relay_returns_text():
    """Smoke-test the contract: relay takes text in, returns text out."""
    adapter = _ConformingAdapter()
    reply = await adapter.relay(member_name="codex", text="hello")
    assert reply == "echo: hello"
