# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-level registry for agents associated with sessions."""

from weakref import WeakValueDictionary

from openjiuwen.core.single_agent.base import BaseAgent


class AgentRuntime:
    """Maintain weak session-to-agent bindings for the current process."""

    def __init__(self) -> None:
        self._agents: WeakValueDictionary[str, BaseAgent] = WeakValueDictionary()

    def bind(self, session_id: str, runtime: BaseAgent) -> None:
        """Bind a session to an agent, replacing any existing binding."""
        self._agents[session_id] = runtime

    def get(self, session_id: str) -> BaseAgent | None:
        """Return the agent bound to a session, if it is still alive."""
        return self._agents.get(session_id)

    def unbind(self, session_id: str, runtime: BaseAgent) -> None:
        """Remove a binding only when it still points to the given agent."""
        if self._agents.get(session_id) is runtime:
            del self._agents[session_id]


_agent_runtime = AgentRuntime()


def get_agent_runtime() -> AgentRuntime:
    """Return the process-level agent runtime registry."""
    return _agent_runtime
