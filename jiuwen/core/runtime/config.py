#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC
from typing import TypedDict, Any, Optional

from jiuwen.agent.config.base import AgentConfig
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.workflow.workflow_config import WorkflowConfig


class MetadataLike(TypedDict):
    name: str
    event: str




class Config(ABC):
    """
    Config is the class defines the basic infos of workflow
    """

    def __init__(self):
        """
        initialize the config
        """
        self._callback_metadata: dict[str, MetadataLike] = {}
        self._env: dict = {}
        self._workflow_configs: dict[str, WorkflowConfig] = {}
        self._agent_config: AgentConfig = None

    def set_envs(self, envs: dict[str, str]) -> None:
        """
        set environment variables
        :param envs: envs
        """
        self._env.update(envs)

    def get_env(self, key: str) -> Any:
        """
        get environment variable by given key
        :param key: environment variable key
        :return: environment variable value
        """
        if key in self._env:
            return self._env[key]
        else:
            return None

    def __load_envs__(self) -> None:
        pass

    def get_workflow_config(self, workflow_id):
        if workflow_id is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_WORKFLOW_CONFIG_GET_FAILED.code,
                                      message=StatusCode.RUNTIME_WORKFLOW_CONFIG_GET_FAILED.errmsg.format(
                                          reason="workflow_id is invalid, cannot be None"))
        return self._workflow_configs.get(workflow_id)

    def get_agent_config(self):
        return self._agent_config

    def set_agent_config(self, agent_config):
        self._agent_config = agent_config

    def add_workflow_config(self, workflow_id, workflow_config):
        if workflow_id is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_WORKFLOW_CONFIG_ADD_FAILED.code,
                                      message=StatusCode.RUNTIME_WORKFLOW_CONFIG_ADD_FAILED.errmsg.format(
                                          reason="workflow_id is invalid, cannot be None"))
        if workflow_config is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_WORKFLOW_CONFIG_ADD_FAILED.code,
                                      message=StatusCode.RUNTIME_WORKFLOW_CONFIG_ADD_FAILED.errmsg.format(
                                          reason="workflow config is invalid, cannot be None"))
        self._workflow_configs[workflow_id] = workflow_config
