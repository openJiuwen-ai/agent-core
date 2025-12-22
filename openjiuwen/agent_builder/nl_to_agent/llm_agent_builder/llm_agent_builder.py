# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from typing import List, Dict, Any

from openjiuwen.agent_builder.nl_to_agent.common.context_manager import ContextManager
from openjiuwen.agent_builder.nl_to_agent.common.llm_service import LlmService
from openjiuwen.agent_builder.nl_to_agent.common.resource.resource_retriever import ResourceRetriever
from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.clarifier.clarifier import Clarifier
from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.generator.generator import Generator
from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.transformer.transformer import Transformer
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class State(Enum):
    INITIAL = 'initial'
    CONSTRUCT = 'construct'


class LlmAgentBuilder:
    def __init__(self, llm: LlmService, context_manager: ContextManager):
        self.llm = llm
        self.context_manager = context_manager
        
        self._state = State.INITIAL
        self._agent_config_info = None
        self._resource = {}

        self._retriever = ResourceRetriever(llm)
        self._clarifier = Clarifier(llm)
        self._generator = Generator(llm)
        self._transformer = Transformer()

    @staticmethod
    def batch_add_unique(exists: List[Dict[str, Any]], news: List[Dict[str, Any]], unique_key="resource_id"):
        if not news:
            return exists
        exist_keys = {item[unique_key] for item in exists if unique_key in item}
        for item in news:
            if item.get(unique_key) not in exist_keys:
                exists.append(item)
                exist_keys.add(item[unique_key])
        return exists

    def execute(self, query: str):
        if self._state == State.INITIAL:
            return self._handle_initial()
        elif self._state == State.CONSTRUCT:
            return self._handle_construct()
        raise JiuWenBaseException(
            StatusCode.NL2AGENT_LLM_AGENT_STATE_ERROR.code,
            StatusCode.NL2AGENT_LLM_AGENT_STATE_ERROR.errmsg.format(error_msg=f"未知的LLM Agent构建阶段：{self._state}")
        )

    def _handle_initial(self):
        dialog_history = self.context_manager.get_history()
        self._update_resource(dialog_history)
        self._agent_config_info = self._clarifier.clarify(dialog_history, resource=self._resource)
        self.context_manager.add_assistant_message(self._agent_config_info)
        self._state = State.CONSTRUCT
        return self._agent_config_info

    def _handle_construct(self):
        dialog_history = self.context_manager.get_history()
        self._update_resource(dialog_history)
        constructor_output = self._generator.generate(dialog_history, self._agent_config_info, resource=self._resource)
        dsl = self._transformer.transform_to_dsl(constructor_output, resource=self._resource)
        self._reset()
        return dsl
    
    def _update_resource(self, dialog_history):
        resource = self._retriever.retrieve(dialog_history)
        for key, value in resource.items():
            if key not in self._resource:
                self._resource.update({key: value})
                continue
            self._resource[key] = self.batch_add_unique(self._resource[key], value)                

    def _reset(self):
        self._state = State.INITIAL
        self._agent_config_info = None
        self._resource = {}