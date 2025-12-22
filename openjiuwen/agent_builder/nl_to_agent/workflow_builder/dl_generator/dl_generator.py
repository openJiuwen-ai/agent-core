# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from typing import Dict, List

from openjiuwen.agent_builder.nl_to_agent.utils.utils import load_yaml_file
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_generator.prompt import generate_system_prompt, \
    refine_user_prompt
from openjiuwen.core.foundation.llm import SystemMessage, HumanMessage

EMPTY_RESOURCE_CONTENT = "无可用工具/资源/外部接口。"


class DLGenerator:
    _RESOURCE_PROMPT_PLACEMENTS = ["plugins"]

    def __init__(self, llm):
        self.llm = llm
        self.reflect_prompts = []
        self.components_info, self.schema_info, self.examples = self.load_schema_and_examples()

    @staticmethod
    def load_schema_and_examples():
        current_dir = os.path.dirname(__file__)

        components_schema_yml = os.path.join(current_dir, "schema.yaml")
        yaml_data = load_yaml_file(components_schema_yml)
        components_info = yaml_data.get("components")
        schema_info = "\n".join([value for key, value in yaml_data.items() if key != "components"])

        examples_yml = os.path.join(current_dir, "examples.yaml")
        examples_data = load_yaml_file(examples_yml)
        examples = examples_data.get("examples")

        return components_info, schema_info, examples

    def generate(self, query: str, resource: Dict[str, List[dict]]) -> str:
        system_prompt = self._update_prompt(resource)
        return self._execute(query, system_prompt)

    def refine(self, query: str, resource: Dict[str, List[dict]], exist_dl: str, exist_mermaid: str) -> str:
        system_prompt = self._update_prompt(resource)
        user_prompt = (refine_user_prompt.replace("{{user_input}}", query)
                                         .replace("{{exist_dl}}", exist_dl)
                                         .replace("{{exist_mermaid}}", exist_mermaid))
        return self._execute(user_prompt, system_prompt)

    def _execute(self, query, system_prompt) -> str:
        prompts = [SystemMessage(content=system_prompt), HumanMessage(content=query)] + self.reflect_prompts
        generated_dl = self.llm.chat(prompts)
        return generated_dl

    def _update_prompt(self, resource: Dict[str, List[dict]]):
        prompt = (generate_system_prompt.replace("{{components}}", self.components_info)
                                        .replace("{{schema}}", self.schema_info)
                                        .replace("{{examples}}", self.examples))
        for key in DLGenerator._RESOURCE_PROMPT_PLACEMENTS:
            value = (resource or {}).get(key)
            if not value:
                prompt = prompt.replace(f"{{{{{key}}}}}", EMPTY_RESOURCE_CONTENT)
                continue
            prompt = prompt.replace(f"{{{{{key}}}}}", "\n".join(str(item) for item in value))
        return prompt
