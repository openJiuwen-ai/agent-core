#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import Optional, Union

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runtime.resources_manager.abstract_manager import AbstractManager
from openjiuwen.core.runner.agent_group import AgentGroup
from openjiuwen.core.agent_group.agent_group import BaseGroup


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
    def create(self) -> AgentGroup:
        pass


class AgentGroupMgr(AbstractManager[AgentGroup]):
    def __init__(self):
        super().__init__()

    def add_agent_group(self, agent_group_id: str, agent_group: Union[AgentGroup, AgentGroupProvider]) -> None:
        self._validate_id(agent_group_id, StatusCode.RUNTIME_AGENT_GROUP_ADD_FAILED, "agent_group")
        
        # Define validation function for non-callable agent groups
        # Support both AgentGroup (legacy) and BaseGroup (new architecture)
        def validate_agent_group(group):
            if not isinstance(group, (AgentGroup, BaseGroup)):
                raise TypeError(
                    f"agent_group must be AgentGroup/BaseGroup instance "
                    f"or callable, got {type(group)}"
                )
            return group
        
        self._add_resource(agent_group_id, agent_group, StatusCode.RUNTIME_AGENT_GROUP_ADD_FAILED, validate_agent_group)

    def remove_agent_group(self, agent_group_id: str) -> Optional[AgentGroup]:
        self._validate_id(agent_group_id, StatusCode.RUNTIME_AGENT_GROUP_REMOVE_FAILED, "agent_group")
        
        return self._remove_resource(agent_group_id, StatusCode.RUNTIME_AGENT_GROUP_REMOVE_FAILED)

    def get_agent_group(self, agent_group_id: str) -> Optional[AgentGroup]:
        self._validate_id(agent_group_id, StatusCode.RUNTIME_AGENT_GROUP_GET_FAILED, "agent_group")
        
        # Define function to create agent group from provider
        # Support both AgentGroup (legacy) and BaseGroup (new architecture)
        def create_group_from_provider(provider):
            group = provider()
            if not isinstance(group, (AgentGroup, BaseGroup)):
                raise TypeError(
                    f"Provider did not return AgentGroup/BaseGroup instance, "
                    f"got {type(group)}"
                )
            return group
        
        return self._get_resource(agent_group_id, StatusCode.RUNTIME_AGENT_GROUP_GET_FAILED, create_group_from_provider)
