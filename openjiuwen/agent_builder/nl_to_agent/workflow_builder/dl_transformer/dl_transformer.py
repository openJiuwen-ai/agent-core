# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from typing import Optional, List, Dict

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters import StartConverter, \
    EndConverter, LLMConverter, IntentDetectionConverter, QuestionerConverter, CodeConverter, PluginConverter, \
    OutputConverter, BranchConverter
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import Workflow, Position, NodeType
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.simpleir_to_mermaid import SimpleIrToMermaid


class DLTransformer:
    _dsl_converter_registry = {
        NodeType.Start.dl_type: StartConverter,
        NodeType.End.dl_type: EndConverter,
        NodeType.LLM.dl_type: LLMConverter,
        NodeType.IntentDetection.dl_type: IntentDetectionConverter,
        NodeType.Questioner.dl_type: QuestionerConverter,
        NodeType.Code.dl_type: CodeConverter,
        NodeType.Plugin.dl_type: PluginConverter,
        NodeType.Output.dl_type: OutputConverter,
        NodeType.Branch.dl_type: BranchConverter,
    }

    @staticmethod
    def collect_plugin(tool_id_list: List[str],
                       plugin_dict: Dict[str, dict],
                       tool_id_map: Dict[str, str]) -> List[dict]:
        collected = []
        for tool_id in tool_id_list:
            if tool_id not in tool_id_map:
                continue

            plugin_id = tool_id_map[tool_id]
            plugin = plugin_dict.get(plugin_id, {})
            tool = plugin.get("tools", {}).get(tool_id, {})
            collected.append({
                "plugin_id": plugin_id,
                "plugin_name": plugin.get("plugin_name", ""),
                "tool_id": tool_id,
                "tool_name": tool.get("tool_name", ""),
                "inputs": tool.get("ori_inputs", []),
                "outputs": tool.get("ori_outputs", []),
            })
        return collected

    def transform_to_mermaid(self, dl_content: str) -> str:
        nodes = json.loads(dl_content)
        mermaid_result = SimpleIrToMermaid.transform_to_mermaid(nodes)
        return mermaid_result

    def transform_to_dsl(self, dl_content: str, resource: Optional[dict] = None) -> str:
        if resource:
            tool_id_list = [item["tool_id"] for item in resource.get("plugins", [])]
            plugins = self.collect_plugin(tool_id_list, resource.get("plugin_dict"), resource.get("tool_id_map"))
            resource["plugins"] = plugins

        nodes = json.loads(dl_content)
        nodes_dict = {node["id"]: node for node in nodes}
        workflow = Workflow()
        x, y = 0, 0
        for node in nodes:
            converter_class = self._dsl_converter_registry.get(node["type"])
            if not converter_class:
                pass # raise JiuwenBaseException not support node_type

            if node["type"] in [NodeType.Plugin.dl_type]:
                node_converter = converter_class(node, nodes_dict, resource=resource, position=Position(x, y))
            else:
                node_converter = converter_class(node, nodes_dict, position=Position(x, y))
            node_converter.convert()
            workflow.nodes.append(node_converter.node)
            workflow.edges.extend(node_converter.edges)
            x += 20
            y += 20
        return json.dumps(ConverterUtils.convert_to_dict(workflow), ensure_ascii=False)
