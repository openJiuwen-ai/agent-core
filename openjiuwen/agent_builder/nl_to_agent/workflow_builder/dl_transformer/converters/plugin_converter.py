# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.base import BaseConverter
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import InputsField


class PluginConverter(BaseConverter):
    @staticmethod
    def _convert_plugin_info(plugin_info):
        return {
            "toolID": plugin_info.get("tool_id"),
            "toolName": plugin_info.get("tool_name"),
            "pluginID": plugin_info.get("plugin_id"),
            "pluginName": plugin_info.get("plugin_name")
        }

    def _convert_specific_config(self):
        plugins = self.resource.get("plugins", [])
        tool_id = self.node_data["parameters"]["configs"]["tool_id"]
        plugin_info = next((p for p in plugins if p.get("tool_id") == tool_id), {})
        plugin_info = self._convert_plugin_info(plugin_info)
        self.node.data.inputs = InputsField(
            inputParameters=self._convert_input_variables(self.node_data["parameters"]["inputs"]),
            pluginParam=plugin_info
        )
        self.node.data.outputs = self._convert_outputs_field(self.node_data["parameters"]["outputs"])
