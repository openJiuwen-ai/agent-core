# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import Optional, Union

from openjiuwen.core.runner.resources_manager.base import AgentProvider
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.server_adapter.agent_adapter import AgentAdapter
from openjiuwen.core.runner.runner_config import get_runner_config
from openjiuwen.core.session import StaticAgentSession
from openjiuwen.core.runner.resources_manager.abstract_manager import AbstractManager


@dataclass
class AgentWithSession:
    session: StaticAgentSession
    agent: BaseAgent


class AgentMgr(AbstractManager[AgentWithSession]):
    _AGENT_ADAPTER = "agent_adapter_"

    def __init__(self):
        super().__init__()

    def add_agent(self, agent_id: str, agent: Union[AgentProvider, RemoteAgent]) -> None:
        if get_runner_config().distributed_mode:
            if not isinstance(agent, RemoteAgent):
                from openjiuwen.core.runner.drunner.server_adapter.agent_adapter import AgentAdapter
                mqAgentAdapter = AgentAdapter(agent_id)
                mqAgentAdapter.start()
                self._add_agent(self._AGENT_ADAPTER + agent_id, mqAgentAdapter)
        self._add_agent(agent_id, agent)

    def _add_agent(self, agent_id: str, agent) -> None:
        self._validate_id(agent_id, StatusCode.SESSION_AGENT_ADD_FAILED, "single_agent")
        self._validate_resource(agent, StatusCode.SESSION_AGENT_ADD_FAILED, "single_agent cannot be None")

        # Define validation function for non-callable agents
        def validate_agent(agent_obj):
            if isinstance(agent, (RemoteAgent, AgentAdapter)):
                if get_runner_config().distributed_mode:
                    return agent
                raise JiuWenBaseException(
                    StatusCode.SESSION_AGENT_ADD_FAILED.code,
                    StatusCode.SESSION_AGENT_ADD_FAILED.errmsg.format(reason="RemoteAgent must be in distributed mode")
                )
            if not hasattr(agent_obj, "config"):
                raise JiuWenBaseException(
                    StatusCode.SESSION_AGENT_ADD_FAILED.code,
                    StatusCode.SESSION_AGENT_ADD_FAILED.errmsg.format(reason="Agent must have config method")
                )
            return AgentWithSession(
                session=StaticAgentSession(config=agent_obj.config()),
                agent=agent_obj
            )

        self._add_resource(agent_id, agent, StatusCode.SESSION_AGENT_ADD_FAILED, validate_agent)

    def remove_agent(self, agent_id: str):
        if get_runner_config().distributed_mode:
            adapter = self._remove_agent(self._AGENT_ADAPTER + agent_id)
            if adapter is not None:
                adapter.stop()
        return self._remove_agent(agent_id)

    def _remove_agent(self, agent_id: str):
        self._validate_id(agent_id, StatusCode.SESSION_AGENT_REMOVE_FAILED, "single_agent")
        self._remove_resource(agent_id, StatusCode.SESSION_AGENT_REMOVE_FAILED)

    def get_agent(self, agent_id: str) -> Optional[AgentWithSession | RemoteAgent]:

        self._validate_id(agent_id, StatusCode.SESSION_AGENT_GET_FAILED, "single_agent")

        # Define function to create single_agent from provider
        def create_agent_from_provider(provider):
            agent = provider()
            if not hasattr(agent, "config"):
                raise JiuWenBaseException(
                    StatusCode.SESSION_AGENT_GET_FAILED.code,
                    StatusCode.SESSION_AGENT_GET_FAILED.errmsg.format(
                        reason="Agent returned by provider must have config method"
                    )
                )
            return AgentWithSession(
                session=StaticAgentSession(config=agent.config()),
                agent=agent
            )

        return self._get_resource(agent_id, StatusCode.SESSION_AGENT_GET_FAILED, create_agent_from_provider)
