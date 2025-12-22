# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import os
import re
from typing import List, Dict

from openjiuwen.agent_builder.nl_to_agent.common.resource.plugin_processor import PluginProcessor
from openjiuwen.agent_builder.nl_to_agent.common.resource.prompt import retrieve_system_prompt
from openjiuwen.agent_builder.nl_to_agent.utils.utils import load_yaml_file
from openjiuwen.core.foundation.llm import SystemMessage


def extract_json(dl: str):
    pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    matches = re.findall(pattern, dl)
    extracted_content = matches[0]
    return extracted_content


class ResourceRetriever:
    def __init__(self, llm):
        self.llm = llm

        raw_plugins = self.load_resources()
        self.plugin_dict, self.tool_plugin_id_map = PluginProcessor.preprocess(raw_plugins)

    @staticmethod
    def load_resources():
        current_dir = os.path.dirname(__file__)
        plugin_yml = os.path.join(current_dir, "plugins.yaml")
        raw_plugins = load_yaml_file(plugin_yml).get("plugins", [])
        return raw_plugins

    def retrieve(self, dialog_history: List[Dict[str, str]], for_workflow: bool = True) -> Dict[str, List[dict]]:
        dialog_history_query = "\n".join(f"{msg['role']}: {msg['content']}" for msg in dialog_history)
        plugin_info_list = PluginProcessor.format_for_prompt(self.plugin_dict)

        system_prompt = (retrieve_system_prompt.replace("{{dialog_history}}", dialog_history_query)
                                               .replace("{{plugin_info_list}}", str(plugin_info_list)))
        data = self._llm_retrieve(system_prompt)
        retrieved_plugin, retrieved_plugin_dict, retrieved_tool_id_map = PluginProcessor.get_retrieved_info(
            data.get("plugin_id_list", []),
            self.plugin_dict,
            self.tool_plugin_id_map,
            need_inputs_outputs=for_workflow
        )
        return dict(plugins=retrieved_plugin, plugin_dict=retrieved_plugin_dict, tool_id_map=retrieved_tool_id_map)
    
    def _llm_retrieve(self, system_prompt: str) -> List[str]:
        prompts = [SystemMessage(content=system_prompt)]
        response = self.llm.chat(prompts)
        return json.loads(extract_json(response))