# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bridge Agent — pure-text protocol adapter to a remote independent agent.

A bridge agent is a full local teammate paired with a remote agent
(claudecode / codex / openclaw / hermes / ...) reachable through some
external protocol (A2A / ACP / local CLI / ...). The local DeepAgent
acts as a scheduler — owning the full teammate tool set, claiming
tasks, deciding when / to whom to reply — while concrete work output
is produced by the remote and relayed back through framework-managed
mailbox auto-forwarding.

``BridgeProtocolAdapter`` is the only extension point the framework
exposes for that relay: a small, pure-text contract. Adapters take a
single text turn and return a single text turn; they never receive
jiuwen Model schemas, tool definitions, or ``ToolCall`` objects, and
they never return ``ToolCall`` objects either. What to do with the
remote's reply is decided by the bridge avatar's local LLM (the
mailbox compose layer wires the reply into the avatar's context next
to the original team message).

Phase-1 contract: shape only. No built-in adapter ships. When no
adapter is registered for a bridge member, the auto-forward path
substitutes :data:`REMOTE_UNAVAILABLE_SENTINEL` for the remote reply
so the bridge degrades to a normal teammate.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = [
    "BridgeAgentNotEnabledError",
    "BridgeProtocolAdapter",
    "REMOTE_UNAVAILABLE_SENTINEL",
    "UnknownBridgeAgentError",
]


REMOTE_UNAVAILABLE_SENTINEL: str = "[remote agent unavailable: no protocol adapter registered]"
"""Sentinel returned by the mailbox compose layer when a bridge member
has no protocol adapter registered. Surfacing a stable string (rather
than ``None``) lets the bridge's local LLM see and react to the
degradation explicitly instead of silently dropping the consultation.
"""


@runtime_checkable
class BridgeProtocolAdapter(Protocol):
    """Pure-text protocol adapter to a remote independent agent.

    Lifecycle (managed by the SDK that owns the bridge member):

    1. :meth:`connect` — open transport, brief the remote with the
       bridge ``persona`` + a short team overview. Called once per
       bridge member after spawn. Exceptions abort bridge setup.
    2. :meth:`relay` — fired by the framework on every team-side
       mailbox event addressed to the bridge. One text turn out,
       one text turn back. Adapters are free to manage any
       internal conversation state the underlying protocol needs.
    3. :meth:`close` — release transport on member shutdown / team
       teardown. Must be idempotent.

    Adapters never see jiuwen-internal data structures (tool schemas,
    ToolCall, Message types). The only currency is text — anything
    richer would couple the protocol surface to the local runtime
    and defeat the "independent entity" contract.
    """

    async def connect(
        self,
        *,
        member_name: str,
        adapter_config: dict[str, object],
        bridge_persona: str,
        team_overview: str,
    ) -> None:
        """Open transport and brief the remote agent.

        Args:
            member_name: Bridge member identity. Lets a single adapter
                instance multiplex multiple bridge members on one
                physical transport when the underlying protocol
                supports it.
            adapter_config: Verbatim ``BridgeMemberSpec.adapter_config``
                from the spec. Adapter implementations decide their
                own schema.
            bridge_persona: The persona the remote should adopt. Comes
                from ``BridgeMemberSpec.persona`` (same persona the
                team sees, ensuring identity is consistent across the
                two sides).
            team_overview: Short text — team name, roster summary,
                each member's role + brief persona — for the remote
                to reference when crafting replies. NOT the full
                jiuwen system prompt; just enough context for the
                remote to know who it's talking to and about.
        """
        ...

    async def relay(
        self,
        *,
        member_name: str,
        text: str,
    ) -> str:
        """Send one text turn to the remote, get one text reply.

        Invoked by the framework on every inbound mailbox message
        addressed to the bridge member (and by no other code path).
        The bridge avatar's local LLM never calls this directly.

        Args:
            member_name: Bridge member identity (multiplexing key).
            text: Outbound payload. Already wrapped per the bridge
                member's ``mailbox_inject_mode`` — adapters do not
                need to re-format.

        Returns:
            Plain text reply from the remote. Empty string is
            allowed (means "no reply needed"); the framework still
            composes it into the bridge avatar's context so the
            avatar knows the forward succeeded but produced no
            content.
        """
        ...

    async def close(self) -> None:
        """Release transport. Must be idempotent.

        Called on member shutdown, team teardown, and adapter swap.
        """
        ...


class BridgeAgentNotEnabledError(RuntimeError):
    """Raised when bridge-agent operations are attempted on a team
    whose ``enable_bridge`` capability ceiling is False.

    Symmetric to ``HumanAgentNotEnabledError``. Inbox / spawn paths
    raise this; the runtime layer catches and converts to a
    ``DeliverResult.failure("bridge_not_enabled")`` at the boundary
    so it never leaks to user code as an opaque exception.
    """


class UnknownBridgeAgentError(RuntimeError):
    """Raised when a member name is treated as a bridge-agent target
    but no such bridge member exists in the team.

    Mostly an internal sanity check — the routing layer normally
    catches this with a cheap ``backend.is_bridge_agent`` test and
    falls back to the teammate path. The exception path covers
    direct-API misuse (e.g. ``backend.set_bridge_adapter`` for an
    unregistered name) where silent ignore would mask bugs.
    """
