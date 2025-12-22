# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.base import BaseConverter
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.models import InputsField


class CodeConverter(BaseConverter):
    CODE_EXCEPTION_CONFIG = {
        "retryTimes": 3,
        "timeoutSeconds": 30,
        "processType": "break"
    }

    def _convert_specific_config(self):
        self.node.data.inputs = InputsField(
            inputParameters=self._convert_input_variables(self.node_data["parameters"]["inputs"]),
            language="python",
            code=self.node_data["parameters"]["configs"]["code"],
        )
        self.node.data.outputs = self._convert_outputs_field(self.node_data["parameters"]["outputs"])
        self.node.data.exceptionConfig = CodeConverter.CODE_EXCEPTION_CONFIG
