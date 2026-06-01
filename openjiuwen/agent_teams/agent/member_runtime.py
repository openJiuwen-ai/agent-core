# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MemberRuntime: the brain seam behind a team member.

``StreamController`` / coordination / the configurator drive a member's
"brain" exclusively through this surface. :class:`~openjiuwen.agent_teams.harness.TeamHarness`
(DeepAgent-backed) is the default implementation; an external CLI agent is
driven by ``ExternalCliRuntime`` implementing the same Protocol. Capturing
the contract here lets the runtime be swapped without business-code changes,
exactly as ``TeamHarness``'s module docstring promises.

Only members actually accessed on a harness *instance* are declared. Rail /
memory / customizer hooks are present because the default DeepAgent path
uses them; an external runtime that skips those features implements them as
no-ops (the configurator never invokes them for such members).
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

# User-facing customizer hook: (agent, member_name, role_value) -> None.
AgentCustomizer = Callable[[Any, Optional[str], str], None]


@runtime_checkable
class MemberRuntime(Protocol):
    """The brain a team member's coordination layer drives."""

    # ---- round runtime surface ----

    def run_streaming(self, inputs: dict[str, Any], *, session_id: Optional[str]) -> AsyncIterator[Any]:
        """Stream chunks for one round, given ``inputs['query']``."""
        ...

    async def steer(self, content: str) -> None:
        """Inject content into the in-flight round (mid-turn)."""
        ...

    async def follow_up(self, content: str) -> None:
        """Queue content to be handled after the current turn."""
        ...

    async def abort(self) -> None:
        """Ask the in-flight round to stop cooperatively."""
        ...

    def init_cwd_for_round(self) -> None:
        """Initialise the per-round working directory, if any."""
        ...

    def has_pending_interrupt(self) -> bool:
        """Return whether the runtime is waiting on an interrupt resume."""
        ...

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        """Return whether ``user_input`` resolves the pending interrupt."""
        ...

    # ---- rail / memory / customizer hooks (default DeepAgent path) ----

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

    def run_agent_customizer(self, customizer: AgentCustomizer) -> None:
        """Invoke a user-supplied customizer hook on the underlying agent."""
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


__all__ = ["AgentCustomizer", "MemberRuntime"]
