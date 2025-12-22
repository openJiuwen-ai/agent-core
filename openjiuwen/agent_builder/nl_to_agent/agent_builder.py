# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.agent_builder.nl_to_agent.common.context_manager import ContextManager
from openjiuwen.agent_builder.nl_to_agent.common.llm_service import LlmService
from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.llm_agent_builder import LlmAgentBuilder
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.workflow_builder import WorkflowBuilder


class AgentBuilderExecutor:
    def __init__(self, query: str, session_id: str, agent_type: str, context_manager_map: dict,
                 llm_agent_builder_map: dict, workflow_builder_map: dict, model_info: dict = None):
        self.query = query
        self.session_id = session_id
        self.agent_type = agent_type
        self.llm = LlmService(model_info)
        self.context_manager = self.get_context_manager(session_id, context_manager_map)
        self.llm_agent_builder = self.get_llm_agent_builder(session_id, llm_agent_builder_map)
        self.workflow_builder = self.get_workflow_builder(session_id, workflow_builder_map)

    @staticmethod
    def get_context_manager(session_id: str, context_manager_map: dict):
        if session_id not in context_manager_map:
            context_manager = ContextManager()
            context_manager_map[session_id] = context_manager
            return context_manager
        return context_manager_map[session_id]

    def get_llm_agent_builder(self, session_id: str, llm_agent_builder_map: dict):
        if session_id not in llm_agent_builder_map:
            llm_agent_builder = LlmAgentBuilder(self.llm, self.context_manager)
            llm_agent_builder_map[session_id] = llm_agent_builder
            return llm_agent_builder
        return llm_agent_builder_map[session_id]

    def get_workflow_builder(self, session_id: str, workflow_builder_map: dict):
        if session_id not in workflow_builder_map:
            workflow_builder = WorkflowBuilder(self.llm, self.context_manager)
            workflow_builder_map[session_id] = workflow_builder
            return workflow_builder
        return workflow_builder_map[session_id]

    def execute(self):
        self.context_manager.add_user_message(self.query)
        if self.agent_type == 'llm_agent':
            return self.llm_agent_builder.execute(self.query)

        elif self.agent_type == 'workflow':
            return self.workflow_builder.execute(self.query)
