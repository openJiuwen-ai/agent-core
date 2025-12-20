#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional

from jiuwen.core.utils.llm.base import BaseChatModel
from jiuwen.core.context_engine.accessor.accessor import ContextAccessor
from jiuwen.core.context_engine.base import ContextOwner
from jiuwen.core.context_engine.context import AgentContext, WorkflowContext
from jiuwen.core.context_engine.config import ContextEngineConfig


class ContextEngine:
    def __init__(self,
                 agent_id: str,
                 config: ContextEngineConfig = None,
                 model: Optional[BaseChatModel] = None,
                 ):
        self._agent_id = agent_id
        self._config = config
        self._context_accessor: ContextAccessor = ContextAccessor(config)
        self._llm: Optional[BaseChatModel] = model

    def get_agent_context(self, session_id: str) -> AgentContext:
        context_owner = ContextOwner(agent_id=self._agent_id, session_id=session_id)
        return AgentContext(context_owner, self._context_accessor)

    def get_workflow_context(self, workflow_id: str, session_id: str) -> WorkflowContext:
        context_owner = ContextOwner(agent_id=self._agent_id, workflow_id=workflow_id, session_id=session_id)
        return WorkflowContext(context_owner, self._context_accessor)
