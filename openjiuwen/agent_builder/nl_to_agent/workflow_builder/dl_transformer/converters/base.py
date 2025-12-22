# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any

from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import NodeType, SourceType, Node, \
    Edge, Position, InputVariable, OutputsField


class BaseConverter(ABC):
    def __init__(self, node_data: Dict[str, Any], nodes_dict: Dict[str, Any], resource: Optional[dict] = None, position: Position = Position(0, 0)):
        self.node_data = node_data
        self.nodes_dict = nodes_dict
        self.resource = resource
        self.position = position
        self.node = Node(id=node_data["id"], type=NodeType[node_data["type"]].dsl_type)
        self.edges = []
        self._variable_index = 0

    @abstractmethod
    def _convert_specific_config(self):
        pass

    def convert(self):
        self._convert_common_config()
        self._convert_specific_config()
        self._convert_edges()

    def _convert_common_config(self):
        self.node.id = self.node_data["id"]
        self.node.meta = {"position": {"x": self.position.x, "y": self.position.y}}
        self.node.data.title = self.node_data["description"]

    def _convert_edges(self):
        if "next" in self.node_data:
            self.edges.append(Edge(sourceNodeID=self.node_data["id"], targetNodeID=self.node_data["next"]))

    def _convert_input_variables(self, inputs) -> Dict[str, InputVariable]:
        result = {}
        for item in inputs:
            if "${" in item["value"]:
                ref_variable = ConverterUtils.convert_ref_variable(item["value"])
                result[item["name"]] = InputVariable(
                    **ref_variable,
                    extra={"index": self._variable_index}
                )
            else:
                result[item["name"]] = InputVariable(
                    type=SourceType.constant,
                    content=item["value"],
                    schema={"type": item.get("type") or "string"},
                    extra={"index": self._variable_index}
                )
            self._variable_index += 1
        return result
    
    def _convert_outputs_field(self, outputs: List[dict]) -> OutputsField:
        result = OutputsField(type="object", properties={}, required=[])
        for item in outputs:
            variable_name_list = item["name"].split("_of_")[::-1]
            result.add_property(variable_name_list, item["description"], self._variable_index, item.get("type"))
            self._variable_index += 1
        return result
