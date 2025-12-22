# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import ast
import json
from typing import Dict, Any, Tuple, List

from openjiuwen.agent_builder.nl_to_agent.llm_agent_builder.clarifier.prompt import \
    FACTOR_SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, RESOURCE_SYSTEM_PROMPT, RESOURCE_USER_PROMPT_TEMPLATE
from openjiuwen.core.foundation.llm import HumanMessage, SystemMessage


class Clarifier:

    RESOURCE_CONFIG = {
        "plugin": {
            "label": "插件", "id_key": "tool_id",
            "name_key": "tool_name", "desc_key": "tool_desc"
        },
        "knowledge": {
            "label": "知识库", "id_key": "knowledge_id",
            "name_key": "knowledge_name", "desc_key": "knowledge_desc"
        },
        "workflow": {
            "label": "工作流", "id_key": "workflow_id",
            "name_key": "workflow_name", "desc_key": "workflow_desc"
        }
    }

    def __init__(self, llm):
        self.llm = llm

    @staticmethod
    def _parse_resource_output(resource_output: str) -> Tuple[str, Dict[str, List[str]]]:
        if "## Agent资源规划" not in resource_output:
            return "", {}

        resource_planning = resource_output.split("## Agent资源规划")[1].strip()

        display_content = []
        id_dict = {}

        for resource_type, config in Clarifier.RESOURCE_CONFIG.items():
            section_start = f"【选择的{config['label']}】"
            if section_start not in resource_planning:
                continue

            section_content = resource_planning.split(section_start)[1].split("【选择")[0].strip()

            try:
                resource_list = ast.literal_eval(section_content)
                if not isinstance(resource_list, list):
                    continue

                valid_resources = []
                id_list = []

                for idx, resource in enumerate(resource_list, 1):
                    if not isinstance(resource, dict):
                        continue

                    name = resource.get(config['name_key'], "")
                    desc = resource.get(config['desc_key'], "")
                    resource_id = resource.get(config['id_key'])

                    if name and desc:
                        valid_resources.append(f"{idx}. {name}：{desc}")
                    if resource_id:
                        id_list.append(resource_id)

                if valid_resources:
                    display_content.append(f"【选择的{config['label']}】\n" + "\n".join(valid_resources))
                    if id_list:
                        id_dict[resource_type] = id_list

            except (SyntaxError, ValueError):
                continue

        return "\n".join(display_content), id_dict

    def clarify(self, messages: str, resource: Dict[Any, Any] )-> Tuple[Any, str, Dict[str, list[str]]]:
        user_prompt = USER_PROMPT_TEMPLATE.replace("{{user_messages}}", messages)

        factor_output = self.llm.chat([SystemMessage(content=FACTOR_SYSTEM_PROMPT), HumanMessage(content=user_prompt)])

        resource_str = json.dumps(resource, ensure_ascii=False, indent=2)
        resource_user_prompt = user_prompt+ (
            RESOURCE_USER_PROMPT_TEMPLATE.replace("{{agent_factor_info}}", factor_output)
            .replace("{{resource}}", resource_str)
        )
        resource_output = self.llm.chat([SystemMessage(content=RESOURCE_SYSTEM_PROMPT), HumanMessage(content=resource_user_prompt)])

        display_resource, resource_id_dict = self._parse_resource_output(resource_output)

        return factor_output, display_resource, resource_id_dict
