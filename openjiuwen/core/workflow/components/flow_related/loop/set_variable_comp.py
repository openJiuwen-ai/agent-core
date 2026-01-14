# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.workflow.components.component import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session import Session, NESTED_PATH_SPLIT, is_ref_path, extract_origin_key
from openjiuwen.core.session import NodeSession


class SetVariableComponent(WorkflowComponent):

    def __init__(self, variable_mapping: dict[str, Any]):
        super().__init__()
        if not variable_mapping:
            raise JiuWenBaseException(StatusCode.COMPONENT_SET_VAR_INIT_FAILED.code,
                                      StatusCode.COMPONENT_SET_VAR_INIT_FAILED.errmsg.format(
                                          error_msg=f'variable_mapping is None or empty'))
        self._variable_mapping = variable_mapping

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        root_session = session.base().parent()
        for left, right in self._variable_mapping.items():
            left_ref_str = extract_origin_key(left)
            keys = left_ref_str.split(NESTED_PATH_SPLIT)

            if len(keys) == 0:
                raise JiuWenBaseException(StatusCode.COMPONENT_SET_VAR_INPUT_PARAM_ERROR.code,
                                          StatusCode.COMPONENT_SET_VAR_INPUT_PARAM_ERROR.errmsg.format(
                                              error_msg=f'key[{left}] not supported format'))

            node_id = keys[0]
            node_session = NodeSession(root_session, node_id)
            node_session.state().set_outputs(SetVariableComponent.generate_output(
                keys[1:], SetVariableComponent.generate_value(session, right)
            ))
        return None

    @staticmethod
    def generate_value(session: Session, value: Any):
        if isinstance(value, str) and is_ref_path(value):
            ref_str = extract_origin_key(value)
            return session.get_global_state(ref_str)
        return value

    @staticmethod
    def generate_output(keys: list[str], value: Any):
        output = value
        for i in range(len(keys) - 1, -1, -1):
            key = keys[i]
            output = {key: output}

        return output
