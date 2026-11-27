# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-level registry for agents associated with sessions."""

from weakref import WeakValueDictionary

from openjiuwen.core.single_agent.base import BaseAgent


class AgentRuntime:
    """Maintain weak bindings for each agent within a process-local session."""

    def __init__(self) -> None:
        self._agents: WeakValueDictionary[tuple[str, str], BaseAgent] = WeakValueDictionary()

    def bind(self, session_id: str, runtime: BaseAgent) -> None:
        """Bind an agent, replacing only the same session and card identity."""
        key = (session_id, runtime.card.id)
        self._agents.pop(key, None)
        self._agents[key] = runtime

    def get(self, session_id: str, *, agent_id: str | None = None) -> BaseAgent | None:
        """Resolve an exact identity, or the session's last live binding.

        An explicit identity never falls back to another member.
        """
        if agent_id is not None:
            return self._agents.get((session_id, agent_id))
        candidates = [agent for (sid, _), agent in self._agents.items() if sid == session_id]
        return candidates[-1] if candidates else None

    def unbind(self, session_id: str, runtime: BaseAgent) -> None:
        """Remove a binding only when it still points to the given agent."""
        key = (session_id, runtime.card.id)
        if self._agents.get(key) is runtime:
            del self._agents[key]


_agent_runtime = AgentRuntime()


def get_agent_runtime() -> AgentRuntime:
    """Return the process-level agent runtime registry."""
    return _agent_runtime
