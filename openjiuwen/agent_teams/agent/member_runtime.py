# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MemberRuntime: the brain seam behind a team member.

``StreamController`` / coordination / the configurator drive a member's
"brain" exclusively through this surface. :class:`~openjiuwen.agent_teams.harness.TeamHarness`
(NativeHarness/DeepAgent-backed) is the default implementation; an external
CLI agent is driven by ``ExternalCliRuntime`` / ``ReinvokeCliRuntime``
implementing the same Protocol. Capturing the contract here lets the runtime
be swapped without business-code changes.

The interaction surface mirrors :class:`~openjiuwen.agent_teams.harness.HarnessProtocol`
(``start`` / ``stop`` / ``outputs`` / ``send`` / ``abort`` / ``pause`` /
``subscribe`` / ``state`` / ``session_id``): one cycle is
started per ``coordination.start`` and torn down at ``finalize_round``. Inputs
arrive through ``send`` (``immediate`` steers the active round); the producing
side fires phase/round events the StreamController maps onto
MemberStatus / ExecutionStatus.

Only members actually accessed on a runtime *instance* are declared. Rail /
memory hooks are present because the default DeepAgent path uses
them; an external runtime that skips those features implements them as no-ops
(the configurator never invokes them for such members).
"""

from __future__ import annotations

from typing import (
    Any,
    AsyncIterator,
    Callable,
    Optional,
    Protocol,
    runtime_checkable,
)


@runtime_checkable
class MemberRuntime(Protocol):
    """The brain a team member's coordination layer drives."""

    # ---- lifecycle ----

    async def start(self, *, team_session: Optional[Any] = None) -> None:
        """Start the runtime's round lifecycle for one run cycle.

        The DeepAgent-backed runtime materialises a fresh harness over a child
        agent session derived from ``team_session`` (so it shares the team
        session id and persisted DeepAgentState). SDK-backed external runtimes
        may likewise derive a member AgentSession for backend-native resume
        state; subprocess-only CLI runtimes may ignore ``team_session``.
        """
        ...

    async def stop(self) -> None:
        """Stop the runtime, cancel in-flight work, and close outputs."""
        ...

    @property
    def state(self) -> Any:
        """Return the current lifecycle phase (a ``HarnessState``)."""
        ...

    @property
    def session_id(self) -> Optional[str]:
        """Return the current session id, or None before ``start``."""
        ...

    # ---- interaction ----

    def outputs(self) -> AsyncIterator[Any]:
        """Return a queue-backed async iterator over output chunks."""
        ...

    async def send(self, content: Any, *, immediate: bool = False) -> Any:
        """Submit input; ``immediate=True`` steers the in-flight round.

        ``content`` may be an ``InteractiveInput`` to resume a pending
        interrupt. Returns a runtime-defined acknowledgement (a sequence id for
        the DeepAgent runtime; None for CLI runtimes).
        """
        ...

    async def abort(self, *, immediate: bool = False) -> None:
        """Abort the in-flight round: graceful (False) or hard+rollback (True)."""
        ...

    async def pause(self) -> None:
        """Pause the in-flight round; the next send restarts it."""
        ...

    async def subscribe(
        self,
        *,
        on_state: Callable[..., Any] | None = None,
        on_round: Callable[..., Any] | None = None,
    ) -> None:
        """Register optional phase/round callbacks; both keyword-only and optional.

        ``on_state`` receives ``old`` / ``new`` (phase) and ``session_id``;
        ``on_round`` receives ``kind`` (started/finished/aborted/paused/failed),
        ``round_id`` and ``result``. Only the non-None callbacks are registered;
        kwargs are narrowed to each callback's declared parameters.
        """
        ...

    # ---- interrupt-resume helpers ----

    def has_pending_interrupt(self) -> bool:
        """Return whether the runtime is waiting on an interrupt resume."""
        ...

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        """Return whether ``user_input`` resolves the pending interrupt."""
        ...

    def init_cwd_for_round(self) -> None:
        """Initialise the per-round working directory, if any."""
        ...

    # ---- rail / memory hooks (default DeepAgent path) ----

    def find_rails(self, rail_type: type) -> list[Any]:
        """Return mounted rails of ``rail_type`` (empty when unsupported)."""
        ...

    async def register_rail(self, rail: Any) -> None:
        """Register an additional rail on the running agent."""
        ...

    async def unregister_rail(self, rail: Any) -> None:
        """Unregister a previously registered rail."""
        ...

    def register_member_tools(self, memory_manager: Any) -> None:
        """Register the team memory toolkit on the agent."""
        ...

    async def inject_member_memory(self, memory_manager: Any, query: str) -> None:
        """Inject loaded memory into the agent's system prompt."""
        ...

    def set_background_task_controller(self, controller: Any) -> None:
        """Attach an external background task controller (pause/resume surface).

        Drives the leader's background swarmflow run; non-DeepAgent runtimes that
        launch no background tools implement it as a no-op.
        """
        ...

    # ---- config snapshots ----

    @property
    def workspace(self) -> Optional[Any]:
        """Return the workspace bound to the runtime, if any."""
        ...

    @property
    def sys_operation(self) -> Optional[Any]:
        """Return the sys_operation bound to the runtime, if any."""
        ...


@runtime_checkable
class SupportsRoundSteering(Protocol):
    """A runtime that can inject text into one specific in-flight round.

    Kept out of :class:`MemberRuntime` on purpose. That Protocol is what *every*
    runtime must satisfy -- ``isinstance`` checks assert exactly that -- and
    round-scoped steering is a capability only some have. Declaring it there made
    ``ExternalCliRuntime`` stop conforming, which is the honest answer to the
    wrong question: the CLI runtime is a perfectly good MemberRuntime that simply
    cannot do this.

    Ask with ``isinstance(runtime, SupportsRoundSteering)`` and report the
    absence as its own outcome. A runtime that cannot steer a round is not the
    same as a round that ended, and telling a user "your instruction arrived
    late" when the truth is "this member can never take one" sends them looking
    for a race that does not exist.

    Method-only on purpose: a Protocol carrying a non-method member cannot be
    used with ``issubclass``, and a class-level check is what a test can assert
    without constructing a runtime. ``active_round`` is therefore not declared
    here; callers that want a round id read it defensively, since it only names
    the round a steer is aimed at.
    """

    async def steer_round(
        self,
        content: str,
        *,
        steer_id: str | None = None,
        expected_round_id: int | None = None,
    ) -> bool:
        """Inject text into the in-flight round, or report there was none.

        Distinct from ``send(content, immediate=True)`` in exactly one way, and
        that way is the whole reason this exists: ``send`` starts a round when
        idle, turning a correction aimed at a finished round into a turn nobody
        asked for. This refuses instead.

        Also distinct from the CLI runtimes' pre-existing ``steer``, which is
        declared ``-> None`` and *buffers* when no turn is in flight -- the same
        silent promotion, one layer down. The names differ so that a runtime
        cannot satisfy this contract by accident.

        Args:
            content: Steering text.
            steer_id: Correlation id surfaced in ``STEER_APPLIED``, so a client
                can tell which of its steers a rail dropped.
            expected_round_id: Refuse unless that specific round is running.
                A live round is not the invariant a caller cares about -- *which*
                round is.

        Returns:
            True when the text was queued against the intended live round.
        """
        ...


__all__ = ["MemberRuntime", "SupportsRoundSteering"]
