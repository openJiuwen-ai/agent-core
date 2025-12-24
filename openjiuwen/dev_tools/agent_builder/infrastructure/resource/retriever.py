#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
from typing import List, Dict

from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.dev_tools.agent_builder.common.utils import load_yaml_file, extract_json_from_text
from openjiuwen.dev_tools.agent_builder.infrastructure.resource.prompt import RETRIEVE_SYSTEM_PROMPT
from openjiuwen.dev_tools.agent_builder.infrastructure.resource.plugin_processer import PluginProcessor


class ResourceRetriever:
    def __init__(self, llm):
        self.llm = llm

        raw_plugins = self.load_resources()
        self.plugin_dict, self.tool_plugin_id_map = PluginProcessor.preprocess(raw_plugins)

    @staticmethod
    def load_resources():
        current_dir = os.path.dirname(__file__)
        plugin_yml = os.path.join(current_dir, "resource.yaml")
        raw_plugins = load_yaml_file(plugin_yml).get("plugins", [])
        return raw_plugins

    def retrieve(self, query: str, for_workflow: bool = True) -> Dict[str, List[dict]]:
        plugin_info_list = PluginProcessor.format_for_prompt(self.plugin_dict)
        messages = RETRIEVE_SYSTEM_PROMPT.format({
            "user_input": query,
            "plugin_info_list": str(plugin_info_list)
        }).to_messages()
        response = self.llm.chat(messages)
        data = JsonUtils.safe_json_loads(extract_json_from_text(response))
        retrieved_plugin, retrieved_plugin_dict, retrieved_tool_id_map = PluginProcessor.get_retrieved_info(
            data.get("plugin_id_list", []),
            self.plugin_dict,
            self.tool_plugin_id_map,
            need_inputs_outputs=for_workflow
        )
        return dict(plugins=retrieved_plugin, plugin_dict=retrieved_plugin_dict, tool_id_map=retrieved_tool_id_map)
