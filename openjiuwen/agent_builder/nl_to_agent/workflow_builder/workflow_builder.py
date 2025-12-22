# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from enum import Enum

from openjiuwen.agent_builder.nl_to_agent.common.context_manager import ContextManager
from openjiuwen.agent_builder.nl_to_agent.common.llm_service import LlmService
from openjiuwen.agent_builder.nl_to_agent.common.resource.resource_retriever import ResourceRetriever
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_generator.dl_generator import DLGenerator
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_reflector.dl_reflector import Reflector
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.dl_transformer import DLTransformer
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.intention_detector.intention_detector import \
    IntentionDetector
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.sop_generator.sop_generator import SopGenerator
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.foundation.llm import AIMessage, HumanMessage

WORKFLOW_REQUEST_CONTENT = "请提供您想要的工作流程描述，以便我为您生成相应的流程图，如果不清楚可以回复不清楚，我将为您规划流程。"
SOP_RESPONSE_CONTENT = "SOP内容如下：\n"
GENERATE_DL_FROM_SOP_CONTENT = "请根据以下SOP内容生成对应的流程定义语言（DL）描述：\n"
MODIFY_DL_CONTENT = "请根据以下错误信息修正流程定义语言（DL）：\n"


class State(Enum):
    INITIAL = 'initial'
    PROCESS_REQUEST = 'process_request'
    PROCESS_CONFIRM = 'process_confirm'


class WorkflowBuilder:
    def __init__(self, llm: LlmService, context_manager: ContextManager):
        self.llm = llm
        self.context_manager = context_manager

        self._state = State.INITIAL
        self._workflow_name = None
        self._workflow_name_en = None
        self._workflow_desc = None
        self._dl = None
        self._mermaid_code = None
        self._resource = None

        self._intention_detector = IntentionDetector(llm)
        self._retriever = ResourceRetriever(llm)
        self._sop_generator = SopGenerator(llm)
        self._dl_generator = DLGenerator(llm)
        self._dl_reflector = Reflector()
        self._dl_transformer = DLTransformer()

    @staticmethod
    def extract_json(dl: str):
        pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
        matches = re.findall(pattern, dl)
        extracted_content = matches[0]
        return extracted_content

    def execute(self, query: str):
        if self._state == State.INITIAL:
            return self._handle_initial(query)
        elif self._state == State.PROCESS_REQUEST:
            return self._handle_process_request(query)
        elif self._state == State.PROCESS_CONFIRM:
            return self._handle_process_confirm(query)
        raise JiuWenBaseException(
            StatusCode.NL2AGENT_WORKFLOW_STATE_ERROR.code,
            StatusCode.NL2AGENT_WORKFLOW_STATE_ERROR.errmsg.format(error_msg=f"未知的工作流阶段：{self._state}")
        )

    def _handle_initial(self, query: str):
        dialog_history = self.context_manager.get_history()
        if not self._intention_detector.detect_initial_instruction(dialog_history):
            self._state = State.PROCESS_REQUEST
            return WORKFLOW_REQUEST_CONTENT

        sop_info = self._sop_generator.transform(query)
        self.context_manager.add_assistant_message(SOP_RESPONSE_CONTENT + sop_info.get("sop"))
        self._resource = self._retriever.retrieve(dialog_history)
        self._dl = self._generate_and_reflect_dl(
            dl_operation=self._dl_generator.generate,
            query=GENERATE_DL_FROM_SOP_CONTENT + sop_info.get("sop"),
            resource=self._resource
        )
        self._mermaid_code = self._dl_transformer.transform_to_mermaid(self._dl)
        self._state = State.PROCESS_CONFIRM
        return self._mermaid_code

    def _handle_process_request(self, query: str):
        dialog_history = self.context_manager.get_history()
        if self._intention_detector.detect_initial_instruction(dialog_history):
            sop_info = self._sop_generator.transform(query)
            self._update_workflow_info(sop_info)
            self.context_manager.add_assistant_message(SOP_RESPONSE_CONTENT + sop_info.get("sop"))
            self._resource = self._retriever.retrieve(dialog_history)
        else:
            self._resource = self._retriever.retrieve(dialog_history)
            sop_info = self._sop_generator.generate(dialog_history, self._resource)
            self._update_workflow_info(sop_info)
            self.context_manager.add_assistant_message(SOP_RESPONSE_CONTENT + sop_info.get("sop"))

        self._dl = self._generate_and_reflect_dl(
            dl_operation=self._dl_generator.generate,
            query=GENERATE_DL_FROM_SOP_CONTENT + sop_info.get("sop"),
            resource=self._resource
        )
        self._mermaid_code = self._dl_transformer.transform_to_mermaid(self._dl)
        self._state = State.PROCESS_CONFIRM
        return self._mermaid_code

    def _handle_process_confirm(self, query: str):
        dialog_history = self.context_manager.get_history()
        if self._intention_detector.detect_refine_intent(dialog_history, self._mermaid_code):
            self._dl = self._generate_and_reflect_dl(
                dl_operation=self._dl_generator.refine,
                query=query,
                resource=self._resource,
                exist_dl=self._dl,
                exist_mermaid=self._mermaid_code
            )
            self._mermaid_code = self._dl_transformer.transform_to_mermaid(self._dl)
            return self._mermaid_code

        dsl = self._dl_transformer.transform_to_dsl(self._dl, self._resource)
        self._reset()
        return dsl

    def _generate_and_reflect_dl(self, dl_operation, max_retries: int = 3, *args, **kwargs):
        for _ in range(max_retries):
            generated_dl = dl_operation(*args, **kwargs)
            generated_dl = self.extract_json(generated_dl)
            self._dl_reflector.check_format(generated_dl)
            if not self._dl_reflector.errors:
                self.context_manager.add_assistant_message(generated_dl)
                return generated_dl
            self._dl_generator.reflect_prompts = [
                AIMessage(content=generated_dl),
                HumanMessage(content=MODIFY_DL_CONTENT + ";\n".join(self._dl_reflector.errors)),
            ]
            self._dl_reflector.reset()

        raise JiuWenBaseException(
            StatusCode.NL2AGENT_WORKFLOW_DL_GENERATION_ERROR.code,
            StatusCode.NL2AGENT_WORKFLOW_DL_GENERATION_ERROR.errmsg.format(
                error_msg="流程定义语言（DL）生成失败，错误信息：" + ";\n".join(self._dl_reflector.errors)
            )
        )

    def _reset(self):
        self._state = State.INITIAL
        self._workflow_name = None
        self._workflow_name_en = None
        self._workflow_desc = None
        self._dl = None
        self._mermaid_code = None
        self._dl_generator.reflect_prompts = []

    def _update_workflow_info(self, sop_info):
        self._workflow_name = sop_info.get("name")
        self._workflow_name_en = sop_info.get("name_en")
        self._workflow_desc = sop_info.get("description")
