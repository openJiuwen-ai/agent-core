# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.exception.exception import JiuWenBaseException

_sentinel = object()


class InteractiveInput(BaseModel):
    # key is id of interaction, value is input for the id
    user_inputs: Dict[str, Any] = Field(default_factory=dict)

    # input not bind to any id, used for the first interaction
    raw_inputs: Any = Field(default=None)

    def __init__(self, raw_inputs: Any = _sentinel):
        super().__init__(**{})
        if raw_inputs is None:
            raise JiuWenBaseException(StatusCode.WORKFLOW_INPUT_INVALID.code,
                StatusCode.WORKFLOW_INPUT_INVALID.errmsg.format
                    (error_msg="value of raw_inputs is none"))
        if raw_inputs is _sentinel:
            self.raw_inputs = None
            return
        self.raw_inputs = raw_inputs

    def update(self, node_id: str, value: Any):
        if self.raw_inputs is not None:
            raise JiuWenBaseException(StatusCode.WORKFLOW_STATE_RUNTIME_ERROR.code,
                StatusCode.WORKFLOW_STATE_RUNTIME_ERROR.errmsg.format
                    (error_msg="raw_inputs existed, update is invalid"))
        if node_id is None or value is None:
            raise JiuWenBaseException(StatusCode.WORKFLOW_INPUT_INVALID.code,
                StatusCode.WORKFLOW_INPUT_INVALID.errmsg.format
                    (error_msg="value is none or node_id is none"))
        self.user_inputs[node_id] = value
