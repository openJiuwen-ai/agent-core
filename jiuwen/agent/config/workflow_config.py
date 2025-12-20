#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Dict, Any

from pydantic import Field

from jiuwen.agent.common.enum import ControllerType
from jiuwen.agent.common.schema import WorkflowSchema
from jiuwen.agent.config.base import AgentConfig
from jiuwen.agent.config.react_config import ConstrainConfig


class WorkflowAgentConfig(AgentConfig):
    controller_type: ControllerType = Field(default=ControllerType.WorkflowController)
    start_workflow: WorkflowSchema = Field(default_factory=WorkflowSchema)
    end_workflow: WorkflowSchema = Field(default_factory=WorkflowSchema)
    global_variables: List[dict] = Field(default_factory=list)
    global_params: Dict[str, Any] = Field(default_factory=dict)

    constrain: ConstrainConfig = Field(default=ConstrainConfig())

    @property
    def is_single_workflow(self) -> bool:
        return len(self.workflows) == 1
