# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.base import BaseConverter
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import Edge, InputsField


class IntentDetectionConverter(BaseConverter):
    @staticmethod
    def _convert_intents(conditions):
        return [
            {"name": cond["expression"].split(" contain ")[1]}
            for cond in conditions if cond["expression"] != "default"
        ]

    @staticmethod
    def _convert_branches(conditions):
        return [{"branchId": cond["branch"]} for cond in conditions]

    def _convert_specific_config(self):
        self.node.data.inputs = InputsField(
            inputParameters=self._convert_input_variables(self.node_data["parameters"]["inputs"]),
            llmParam=ConverterUtils.convert_llm_param(self.node_data["parameters"]["configs"]["prompt"], ""),
            intents=self._convert_intents(self.node_data["parameters"]["conditions"])
        )
        self.node.data.outputs = self._convert_outputs_field(
            [{"name": "classificationId", "type": "integer", "description": None}]
        )
        self.node.data.outputs.required.append("classificationId")
        self.node.data.branches = self._convert_branches(self.node_data["parameters"]["conditions"])

    def _convert_edges(self):
        for cond in self.node_data["parameters"]["conditions"]:
            self.edges.append(
                Edge(sourceNodeID=self.node_data["id"], targetNodeID=cond["next"], sourcePortID=cond["branch"])
            )