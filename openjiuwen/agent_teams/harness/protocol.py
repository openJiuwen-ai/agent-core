# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HarnessProtocol: the concurrent-safe interaction contract for a harness.

Defines the external interaction surface independent of how a harness drives
the underlying agent. ``NativeHarness`` (which drives a DeepAgent's task loop)
implements it directly; ``TeamHarness`` composes a single ``NativeHarness`` and
forwards this surface, so callers program against the contract rather than a
concrete class. The broader brain seam the team coordination layer drives —
:class:`~openjiuwen.agent_teams.agent.member_runtime.MemberRuntime` — supersets
this contract with team-specific rail / memory hooks; CLI-backed
member runtimes implement that wider surface for non-DeepAgent brains.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Protocol, runtime_checkable

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.agent_teams.harness.state import HarnessState

if TYPE_CHECKING:
    from openjiuwen.core.session.interaction.interactive_input import InteractiveInput


@runtime_checkable
class HarnessProtocol(Protocol):
    """Concurrent-safe multi-round interaction contract for a harness.

    All methods are concurrent-safe by contract: callers may invoke them from
    any coroutine without external locking. The two send/abort knobs
    (``immediate``) and ``pause`` form the full interaction vocabulary;
    ``outputs`` is the single streaming channel.
    """

    @property
    def state(self) -> HarnessState:
        """Return the current lifecycle phase."""
        ...

    @property
    def session_id(self) -> str | None:
        """Return the owned/injected session id, or None before ``start``."""
        ...

    async def start(self, *, session: Session | None = None) -> None:
        """Initialize the harness and start its supervisor."""
        ...

    async def stop(self) -> None:
        """Stop the harness, cancel in-flight work, and close outputs."""
        ...

    def outputs(self) -> AsyncIterator[OutputSchema]:
        """Return a queue-backed async iterator over output chunks."""
        ...

    async def send(self, content: "str | InteractiveInput", *, immediate: bool = False) -> str:
        """Submit input; ``immediate=True`` injects into the current round.

        ``content`` may be an ``InteractiveInput`` to resume a pending
        interrupt; the harness starts a single-round resume for it (``immediate``
        is ignored for a resume payload). Returns the monotonic sequence id
        assigned to the message.
        """
        ...

    async def abort(self, *, immediate: bool = False) -> None:
        """Abort the current round: graceful (False) or hard+rollback (True)."""
        ...

    async def pause(self) -> None:
        """Pause the current round; the next send concatenates and restarts it."""
        ...


__all__ = ["HarnessProtocol"]
