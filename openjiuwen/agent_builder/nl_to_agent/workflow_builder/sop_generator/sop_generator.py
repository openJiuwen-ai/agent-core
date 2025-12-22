# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from typing import Dict, List

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.sop_generator.prompt import transform_system_prompt, \
    generate_system_prompt
from openjiuwen.core.foundation.llm import SystemMessage, HumanMessage

SOP_GENERATE_PROMPT = "请根据以下对话历史设计工作流程：\n"
EMPTY_RESOURCE_CONTENT = "无可用工具/资源/外部接口。"


class SopGenerator:
    _EXTRACT_ELEMENTS = {"name": "任务中文名称", "name_en": "任务英文名称", "description": "任务介绍", "sop": "流程"}

    def __init__(self, llm):
        self.llm = llm

    @staticmethod
    def parse_info(content: str) -> Dict[str, str]:
        info_dict = {}
        for key, tag in SopGenerator._EXTRACT_ELEMENTS.items():
            pattern = re.compile(rf"<{tag}>([^<]*?)</{tag}>", re.DOTALL)
            match = pattern.search(content)
            info_dict[key] = match.group(1).strip() if match else ""
        return info_dict
    
    def transform(self, query: str) -> str:
        return self._execute(query, transform_system_prompt)

    def generate(self, dialog_history, resource: Dict[str, List[dict]]) -> str:
        dialog_history_query = "\n".join(f"{msg['role']}: {msg['content']}" for msg in dialog_history)
        dialog_history_query = SOP_GENERATE_PROMPT + dialog_history_query
        system_prompt = self._update_prompt(resource)
        return self._execute(dialog_history_query, system_prompt)

    def _execute(self, query: str, system_prompt: str) -> str:
        prompts = [SystemMessage(content=system_prompt), HumanMessage(content=query)]
        generated_sop = self.llm.chat(prompts)
        sop_info = self.parse_info(generated_sop)
        return sop_info

    def _update_prompt(self, resource: Dict[str, List[dict]]):
        plugins = (resource or {}).get("plugins")
        if not plugins:
            return generate_system_prompt.replace("{{plugins}}", EMPTY_RESOURCE_CONTENT)
        return generate_system_prompt.replace("{{plugins}}", "\n".join(str(p) for p in plugins))
    
