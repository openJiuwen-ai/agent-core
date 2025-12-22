# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import Optional, Union

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runner.resources_manager.abstract_manager import AbstractManager
from openjiuwen.core.multi_agent import BaseGroup


class AgentGroupProvider(ABC):
    def __init__(self):
        self._subscription = None

    @abstractmethod
    def get_topic(self):
        pass

    def set_subscription(self, subscription):
        self._subscription = subscription

    def __call__(self):
        agent_group = self.create()
        agent_group.set_subscription(self._subscription)
        return agent_group

    @abstractmethod
    def create(self) -> BaseGroup:
        pass


class AgentGroupMgr(AbstractManager[BaseGroup]):
    def __init__(self):
        super().__init__()

    async def add_agent_group(self, agent_group_id: str, agent_group: Union[BaseGroup, AgentGroupProvider]):
        self._add_agent_group(agent_group_id, agent_group)
        # Only subscribe to message queue for AgentGroup with get_topic method
        # BaseGroup (HierarchicalGroup etc.) uses different message mechanism
        if hasattr(agent_group, 'get_topic') and callable(agent_group.get_topic):
            topic = agent_group.get_topic()
            if topic is not None:
                from openjiuwen.core.runner import Runner
                subscription = await Runner.pubsub.subscribe(topic)
                agent_group.set_subscription(subscription)

    async def remove_agent_group(self, agent_group_id: str) -> Union[BaseGroup, AgentGroupProvider]:
        agent_group = self._remove_agent_group(agent_group_id)
        # Only unsubscribe for AgentGroup with get_topic method and subscription
        if agent_group and hasattr(agent_group, 'get_topic') and callable(agent_group.get_topic):
            topic = agent_group.get_topic()
            if topic is not None and hasattr(agent_group, '_subscription'):
                from openjiuwen.core.runner import Runner
                await Runner.pubsub.unsubscribe(topic, agent_group.get_subscription)
        return agent_group

    def _add_agent_group(self, agent_group_id: str, agent_group: Union[AgentGroupProvider]) -> None:
        self._validate_id(agent_group_id, StatusCode.SESSION_AGENT_GROUP_ADD_FAILED, "multi_agent")

        # Define validation function for non-callable single_agent groups
        # Support both AgentGroup (legacy) and BaseGroup (new architecture)
        def validate_agent_group(group):
            if not isinstance(group, BaseGroup):
                raise TypeError(
                    f"multi_agent must be AgentGroup/BaseGroup instance "
                    f"or callable, got {type(group)}"
                )
            return group

        self._add_resource(agent_group_id, agent_group, StatusCode.SESSION_AGENT_GROUP_ADD_FAILED, validate_agent_group)

    def _remove_agent_group(self, agent_group_id: str) -> Optional[BaseGroup]:
        self._validate_id(agent_group_id, StatusCode.SESSION_AGENT_GROUP_REMOVE_FAILED, "multi_agent")
        return self._remove_resource(agent_group_id, StatusCode.SESSION_AGENT_GROUP_REMOVE_FAILED)

    def get_agent_group(self, agent_group_id: str) -> Optional[BaseGroup]:
        self._validate_id(agent_group_id, StatusCode.SESSION_AGENT_GROUP_GET_FAILED, "multi_agent")

        # Define function to create single_agent group from provider
        # Support both AgentGroup (legacy) and BaseGroup (new architecture)
        def create_group_from_provider(provider):
            group = provider()
            if not isinstance(group, BaseGroup):
                raise TypeError(
                    f"Provider did not return AgentGroup/BaseGroup instance, "
                    f"got {type(group)}"
                )
            return group

        return self._get_resource(agent_group_id, StatusCode.SESSION_AGENT_GROUP_GET_FAILED, create_group_from_provider)
