# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.base import BaseConverter
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import InputsField


class QuestionerConverter(BaseConverter):
    def _convert_specific_config(self):
        llmParam = ConverterUtils.convert_llm_param(self.node_data["parameters"]["configs"]["prompt"], "")
        self.node.data.inputs = InputsField(
            inputParameters=self._convert_input_variables(self.node_data["parameters"]["inputs"]),
            llmParam=llmParam,
            systemPrompt=llmParam["systemPrompt"]
        )
        outputs = self._convert_outputs_field(self.node_data["parameters"]["outputs"])
        self.node.data.outputs = outputs
        self.node.data.outputs.required=[key for key in outputs.properties.keys()]
