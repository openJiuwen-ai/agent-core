# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from typing import List, Dict, Any

from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.generator.prompt import \
    GENERATE_USER_PROMPT_TEMPLATE, GENERATE_SYSTEM_PROMPT
from openjiuwen.core.foundation.llm import HumanMessage, SystemMessage


class Generator:

    _EXTRACT_ELEMENTS = {
        "name": "角色名称", "description": "角色描述", "prompt": "提示词",
        "opening_remarks": "智能体开场白", "question": "预置问题",
    }

    def __init__(self, llm):
        self.llm = llm

    @staticmethod
    def _parse_info(content: str) -> Dict[str, Any]:
        def _parse_element(content: str, key: str) -> str:
            pattern = rf'<{key}>(.*?)</{key}>'
            match = re.search(pattern, content, re.DOTALL)
            return match.group(1).strip() if match else ""

        info_dict = {}
        for key, value in Generator._EXTRACT_ELEMENTS.items():
            extracted_content = _parse_element(content, value)
            info_dict[key] = extracted_content

        return info_dict

    def generate(self,
                 message: str,
                 agent_config_info: str,
                 agent_resource_info: str,
                 resource_id_dict: Dict[str, List[str]]) -> Dict[str, Any]:

        user_prompt = (
            GENERATE_USER_PROMPT_TEMPLATE.replace("{{user_message}}", message)
            .replace("{{agent_config_info}}", agent_config_info)
            .replace("{{agent_resource_info}}", agent_resource_info)
        )

        generated_content = self.llm.chat([SystemMessage(content=GENERATE_SYSTEM_PROMPT), HumanMessage(content=user_prompt)])

        content_parse = self._parse_info(generated_content)
        content_parse.update(resource_id_dict)

        return content_parse
