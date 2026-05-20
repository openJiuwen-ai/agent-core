# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CLI-backed member runtimes: the P2 counterparts to ``TeamHarness``.

Instead of driving a local DeepAgent, these drive an external CLI as the
member's brain. Two flavours implement the same :class:`MemberRuntime`
surface, picked by the adapter's ``supports_stdin_injection``:

* :class:`ExternalCliRuntime` — **streaming**. One long-lived subprocess; a
  round delivers the inbound text to its stdin and reads stdout until the
  per-CLI adapter signals the turn is complete. Supports mid-turn steer.
* :class:`ReinvokeCliRuntime` — **one-shot / re-invoke-per-turn**. A fresh
  subprocess per turn (prompt passed as argv, read stdout to EOF); for CLIs
  that take the prompt as a flag and exit (openclaw, hermes). No mid-turn
  steer — messages arriving during a turn are buffered and drained as
  follow-up re-invocations before the round ends.

In both cases the CLI's *actions* (messages, task ops) flow out-of-process
through the team CLI/MCP tools, so the CLI's stdout stays internal and is
not surfaced as team-stream chunks. Rail / memory / customizer hooks are
no-ops (the configurator skips those features for external CLI members).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional

from openjiuwen.agent_teams.agent.member_runtime import AgentCustomizer
from openjiuwen.agent_teams.external.cli_agent.adapters import CliAgentAdapter
from openjiuwen.agent_teams.external.cli_agent.injector import Injector
from openjiuwen.core.common.logging import team_logger

# Joins buffered mid-turn messages into a single follow-up prompt.
_FOLLOWUP_SEP = "\n\n---\n\n"


async def _terminate(process: Optional[asyncio.subprocess.Process]) -> None:
    """Terminate a subprocess if still running. Idempotent and quiet."""
    if process is None or process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    with contextlib.suppress(Exception):
        await process.wait()


class _CliRuntimeBase(ABC):
    """Shared :class:`MemberRuntime` surface for CLI-backed members."""

    def __init__(self, *, member_name: str, adapter: CliAgentAdapter):
        self._member_name = member_name
        self._adapter = adapter

    async def run_streaming(self, inputs: dict[str, Any], *, session_id: Optional[str]) -> AsyncIterator[Any]:
        """Drive one round; yields nothing (CLI stdout stays internal)."""
        await self._drive(inputs)
        for _never in ():  # pragma: no cover - empty: makes this an async generator
            yield _never

    @abstractmethod
    async def _drive(self, inputs: dict[str, Any]) -> None:
        """Execute one round against the CLI, returning when the turn ends."""

    @abstractmethod
    async def steer(self, content: str) -> None:
        """Deliver content to the in-flight round (or buffer it)."""

    @abstractmethod
    async def follow_up(self, content: str) -> None:
        """Deliver content to be handled after the current turn."""

    @abstractmethod
    async def abort(self) -> None:
        """Request the in-flight round to stop."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release the CLI transport. Idempotent."""

    # ---- MemberRuntime no-op hooks (external CLI members have none) ----

    def init_cwd_for_round(self) -> None:
        """No-op: the subprocess owns its working directory."""
        return None

    def has_pending_interrupt(self) -> bool:
        """External CLI members have no interrupt-resume concept."""
        return False

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        """External CLI members have no interrupt-resume concept."""
        return False

    def find_rails(self, rail_type: type) -> list[Any]:
        """No rails on a CLI-backed runtime."""
        return []

    async def register_rail(self, rail: Any) -> None:
        """No-op: CLI-backed runtime has no rail stack."""
        return None

    async def unregister_rail(self, rail: Any) -> None:
        """No-op: CLI-backed runtime has no rail stack."""
        return None

    def register_member_tools(self, memory_manager: Any) -> None:
        """No-op: external CLI members do not use the team memory toolkit."""
        return None

    async def inject_member_memory(self, memory_manager: Any, query: str) -> None:
        """No-op: external CLI members do not use team memory injection."""
        return None

    def run_agent_customizer(self, customizer: AgentCustomizer) -> None:
        """No-op: the agent_customizer hook targets a local DeepAgent."""
        return None

    @property
    def workspace(self) -> Optional[Any]:
        """External CLI runtime exposes no team workspace handle."""
        return None

    @property
    def sys_operation(self) -> Optional[Any]:
        """External CLI runtime exposes no sys_operation handle."""
        return None


class ExternalCliRuntime(_CliRuntimeBase):
    """Streaming runtime: one long-lived CLI subprocess driven via stdin."""

    def __init__(
        self,
        *,
        member_name: str,
        adapter: CliAgentAdapter,
        injector: Injector,
        output_lines: AsyncIterator[str],
        process: Optional[asyncio.subprocess.Process] = None,
    ):
        """Bind to a launched CLI subprocess's input/output channels."""
        super().__init__(member_name=member_name, adapter=adapter)
        self._injector = injector
        self._output_lines = output_lines
        self._process = process
        self._abort_requested = False

    async def _drive(self, inputs: dict[str, Any]) -> None:
        query = inputs.get("query")
        text = query if isinstance(query, str) else str(query)
        self._abort_requested = False
        await self._injector.write(self._adapter.format_input(text))
        async for line in self._output_lines:
            if self._abort_requested:
                team_logger.debug("[{}] external cli turn aborted", self._member_name)
                return
            if self._adapter.is_turn_complete(line):
                return

    async def steer(self, content: str) -> None:
        """Inject content into the running CLI mid-turn."""
        await self._injector.write(self._adapter.format_input(content))

    async def follow_up(self, content: str) -> None:
        """Inject content for the CLI to handle after the current turn."""
        await self._injector.write(self._adapter.format_input(content))

    async def abort(self) -> None:
        """Stop the in-flight turn at the next output line (process survives)."""
        self._abort_requested = True

    async def aclose(self) -> None:
        """Close stdin and terminate the long-lived subprocess. Idempotent."""
        await self._injector.aclose()
        await _terminate(self._process)


class ReinvokeCliRuntime(_CliRuntimeBase):
    """One-shot runtime: a fresh CLI subprocess per turn (prompt as argv).

    Messages that arrive mid-turn (via steer/follow_up) cannot interrupt a
    one-shot process; they are buffered and drained as follow-up
    re-invocations within the same round before it returns, so nothing is
    lost. A turn completes at the subprocess's stdout EOF / exit.
    """

    def __init__(
        self,
        *,
        member_name: str,
        adapter: CliAgentAdapter,
        env: dict[str, str],
        cwd: Optional[str] = None,
        cli_session_id: Optional[str] = None,
    ):
        """Hold the launch config; subprocesses are created per turn."""
        super().__init__(member_name=member_name, adapter=adapter)
        self._env = env
        self._cwd = cwd
        self._cli_session_id = cli_session_id or uuid.uuid4().hex
        self._first_turn = True
        self._pending: list[str] = []
        self._aborted = False
        self._current: Optional[asyncio.subprocess.Process] = None

    async def _drive(self, inputs: dict[str, Any]) -> None:
        query = inputs.get("query")
        prompt: Optional[str] = query if isinstance(query, str) else str(query)
        self._aborted = False
        while prompt is not None:
            await self._run_once(prompt)
            if self._aborted:
                return
            prompt = self._drain_pending()

    async def _run_once(self, prompt: str) -> None:
        argv = self._adapter.build_turn_command(
            prompt,
            session_id=self._cli_session_id,
            first_turn=self._first_turn,
        )
        self._first_turn = False
        team_logger.info("[external-cli] re-invoke {} for member {}", argv, self._member_name)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        self._current = proc
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            if proc.stdout is not None:
                while True:
                    line = await proc.stdout.readline()
                    if not line or self._aborted:
                        break
            await proc.wait()
        finally:
            self._current = None

    def _drain_pending(self) -> Optional[str]:
        if not self._pending:
            return None
        combined = self._pending[0] if len(self._pending) == 1 else _FOLLOWUP_SEP.join(self._pending)
        self._pending = []
        return combined

    async def steer(self, content: str) -> None:
        """Buffer content for a follow-up re-invocation (no mid-turn steer)."""
        self._pending.append(content)

    async def follow_up(self, content: str) -> None:
        """Buffer content for a follow-up re-invocation."""
        self._pending.append(content)

    async def abort(self) -> None:
        """Terminate the current turn's subprocess and stop the round."""
        self._aborted = True
        await _terminate(self._current)

    async def aclose(self) -> None:
        """Terminate any in-flight subprocess. Idempotent."""
        await _terminate(self._current)


__all__ = ["ExternalCliRuntime", "ReinvokeCliRuntime"]
