# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converter_utils import ConverterUtils
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.base import BaseConverter
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import InputsField


class LLMConverter(BaseConverter):
    def _convert_specific_config(self):
        self.node.data.inputs = InputsField(
            inputParameters=self._convert_input_variables(self.node_data["parameters"]["inputs"]),
            llmParam=ConverterUtils.convert_llm_param(
                self.node_data["parameters"]["configs"]["system_prompt"],
                self.node_data["parameters"]["configs"]["user_prompt"]
            )
        )
        self.node.data.outputs = self._convert_outputs_field(self.node_data["parameters"]["outputs"])
