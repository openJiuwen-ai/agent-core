# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.agent_builder.nl_to_agent.workflow_builder.dl_transformer.converters.base import BaseConverter


class StartConverter(BaseConverter):
    def _convert_specific_config(self):
        self.node.data.outputs = self._convert_outputs_field(self.node_data["parameters"]["outputs"])
