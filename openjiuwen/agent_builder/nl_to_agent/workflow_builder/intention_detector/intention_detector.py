# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import re
from typing import List, Dict, Any

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.intention_detector.intention_prompt import \
    REFINE_INTENTION_PROMPT, INITIAL_INTENTION_PROMPT
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.foundation.llm import SystemMessage



class IntentionDetector:

    ROLE = "role"
    CONTENT = "content"
    ROLE_MAP = {
        'user': '用户',
        'assistant': '助手',
        'system': '系统'
    }

    def __init__(self, model):
        self.model = model

    def _format_dialog_history(self, dialog_history: List[Dict[str, Any]]) -> str:
        formatted_lines = []
        for msg in dialog_history:
            role = msg.get(self.ROLE)
            content = msg.get(self.CONTENT)
            role_display = self.ROLE_MAP.get(role, '用户')
            formatted_lines.append(f"{role_display}：{content}")
        return "\n".join(formatted_lines)

    def _extract_intent(self, inputs: str) -> Dict[str, Any]:
        json_pattern = r"```json\n(.*?)```"
        json_match = re.search(json_pattern, inputs, re.DOTALL)
        result = json_match.group(1) if json_match else inputs
        return json.loads(result)

    def detect_initial_instruction(self, messages: List[Dict[str, Any]]) -> bool:
        try:
            if not messages:
                return False
            formatted_history = self._format_dialog_history(messages)
            prompt = INITIAL_INTENTION_PROMPT.replace('{{dialog_history}}', formatted_history)
            model_response = self.model.chat([SystemMessage(content=prompt)], method="invoke", add_prefix=False)
            operation_result = self._extract_intent(model_response)
            return operation_result.get("provide_process", False)
        except Exception:
            raise JiuWenBaseException(
                StatusCode.NL2AGENT_WORKFLOW_INTENTION_DETECT_ERROR.code,
                StatusCode.NL2AGENT_WORKFLOW_INTENTION_DETECT_ERROR.errmsg.format(error_msg="NL2Workflow流程意图判断出现异常"),
            )

    def detect_refine_intent(self, messages: List[Dict[str, Any]], flowchart_code: str) -> bool:
        try:
            if not messages:
                return False
            formatted_history = self._format_dialog_history(messages)
            prompt = (
                REFINE_INTENTION_PROMPT
                .replace('{{mermaid_code}}', flowchart_code)
                .replace('{{dialog_history}}', formatted_history)
            )
            model_response = self.model.chat([SystemMessage(content=prompt)], method="invoke", add_prefix=False)
            operation_result = self._extract_intent(model_response)
            return operation_result.get("need_refined", False)
        except Exception:
            raise JiuWenBaseException(
                StatusCode.NL2AGENT_WORKFLOW_INTENTION_DETECT_ERROR.code,
                StatusCode.NL2AGENT_WORKFLOW_INTENTION_DETECT_ERROR.errmsg.format(error_msg="NL2Workflow流程意图判断出现异常"),
            )
