# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""P2PAbilityManager -- AbilityManager for HierarchicalTeam supervisors."""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Tuple, Union, TYPE_CHECKING

from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

if TYPE_CHECKING:
    from openjiuwen.core.multi_agent.team_runtime.communicable_agent import CommunicableAgent


class P2PAbilityManager(AbilityManager):
    """AbilityManager that routes AgentCard tool calls via TeamRuntime P2P send().

    AgentCard calls are dispatched in parallel, bounded by
    ``max_parallel_sub_agents``.  All other ability types are forwarded to
    the base-class ``execute()`` unchanged.

    Args:
        supervisor:              The supervisor agent whose ``send()`` is used for P2P dispatch.
        max_parallel_sub_agents: Max concurrent AgentCard dispatches per :meth:`execute` call.
    """

    def __init__(
        self,
        supervisor: "CommunicableAgent",
        max_parallel_sub_agents: int = 10,
    ) -> None:
        super().__init__()
        self._supervisor = supervisor
        self._max_parallel_sub_agents = max(1, max_parallel_sub_agents)
        # Semaphore is created lazily so it binds to the correct running event loop.
        self._agent_semaphore: Optional[asyncio.Semaphore] = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Return (and lazily create) the per-loop semaphore."""
        if self._agent_semaphore is None:
            self._agent_semaphore = asyncio.Semaphore(self._max_parallel_sub_agents)
        return self._agent_semaphore

    # ------------------------------------------------------------------
    # Parallel execute override
    # ------------------------------------------------------------------

    async def execute(
        self,
        ctx: AgentCallbackContext,
        tool_call: Union[ToolCall, List[ToolCall]],
        session: Session,
        tag=None,
    ) -> List[Tuple[Any, ToolMessage]]:
        """Execute tool calls, dispatching AgentCard calls via P2P and others via super().

        Args:
            ctx:       Callback context for tool-call lifecycle hooks.
            tool_call: Single ToolCall or list of ToolCalls from the LLM.
            session:   Current agent session.
            tag:       Optional resource tag forwarded to base class.

        Returns:
            List of ``(result, ToolMessage)`` tuples in the original call order.
        """
        tool_calls = self._normalize_tool_calls(tool_call)
        if not tool_calls:
            return []

        # Partition into agent-calls vs. non-agent calls.
        agent_indices = [
            i for i, tc in enumerate(tool_calls) if tc.name in self._agents
        ]
        other_indices = [
            i for i in range(len(tool_calls)) if i not in set(agent_indices)
        ]

        # Fast path: no agent calls -- delegate entirely to base class.
        if not agent_indices:
            return await super().execute(ctx, tool_calls, session, tag)

        semaphore = self._get_semaphore()

        async def _dispatch_one(tc: ToolCall) -> Tuple[Any, ToolMessage]:
            async with semaphore:
                return await self._execute_single_tool_call(tc, session, tag)

        agent_coros = [_dispatch_one(tool_calls[i]) for i in agent_indices]
        other_coro = (
            super().execute(ctx, [tool_calls[i] for i in other_indices], session, tag)
            if other_indices
            else None
        )

        if other_coro is not None:
            agent_raw, other_results = await asyncio.gather(
                asyncio.gather(*agent_coros, return_exceptions=True),
                other_coro,
            )
        else:
            agent_raw = await asyncio.gather(*agent_coros, return_exceptions=True)
            other_results = []

        # Resolve agent results (convert exceptions to error ToolMessages).
        agent_results: List[Tuple[Any, ToolMessage]] = []
        for raw, idx in zip(agent_raw, agent_indices):
            if isinstance(raw, Exception):
                error_msg = f"P2P parallel dispatch failed: {raw}"
                logger.error(
                    f"[{self.__class__.__name__}] {error_msg}", exc_info=raw
                )
                agent_results.append(
                    (
                        None,
                        ToolMessage(
                            content=error_msg,
                            tool_call_id=tool_calls[idx].id,
                        ),
                    )
                )
            else:
                agent_results.append(raw)

        # Reconstruct results in original tool_call order.
        final: List[Optional[Tuple[Any, ToolMessage]]] = [None] * len(tool_calls)
        for result, idx in zip(agent_results, agent_indices):
            final[idx] = result
        for result, idx in zip(other_results, other_indices):
            final[idx] = result

        logger.debug(
            f"[{self.__class__.__name__}] parallel dispatch complete: "
            f"{len(agent_indices)} agent call(s) / "
            f"{len(other_indices)} other call(s) / "
            f"max_parallel={self._max_parallel_sub_agents}"
        )
        return [r for r in final if r is not None]  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Single-call P2P dispatch
    # ------------------------------------------------------------------

    async def _execute_single_tool_call(
        self,
        tool_call: ToolCall,
        session: Session,
        tag=None,
    ) -> Tuple[Any, ToolMessage]:
        """Route AgentCard calls via P2P send; delegate all others to super()."""
        tool_name = tool_call.name

        if tool_name not in self._agents:
            return await super()._execute_single_tool_call(tool_call, session, tag)

        import json as _json

        agent_card = self._agents[tool_name]
        try:
            tool_args = (
                _json.loads(tool_call.arguments)
                if isinstance(tool_call.arguments, str)
                else (tool_call.arguments or {})
            )
        except (ValueError, TypeError):
            tool_args = {}

        session_id = session.get_session_id() if session is not None else None

        logger.debug(
            f"[{self.__class__.__name__}] P2P dispatch "
            f"tool='{tool_name}' agent_id='{agent_card.id}' session_id={session_id!r}"
        )

        try:
            result = await self._supervisor.send(
                message=tool_args,
                recipient=agent_card.id,
                session_id=session_id,
            )
        except Exception as exc:
            error_msg = f"P2P dispatch to '{tool_name}' failed: {exc}"
            logger.warning(f"[{self.__class__.__name__}] {error_msg}")
            raise self._build_execution_error(tool_call, error_msg) from exc

        tool_message = ToolMessage(
            content=str(result),
            tool_call_id=tool_call.id,
        )
        return result, tool_message


__all__ = ["P2PAbilityManager"]
