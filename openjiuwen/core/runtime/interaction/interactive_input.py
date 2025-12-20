#!/usr/bin/env python
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
            raise JiuWenBaseException(StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.code,
                                      StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.errmsg)
        if raw_inputs is _sentinel:
            self.raw_inputs = None
            return
        self.raw_inputs = raw_inputs

    def update(self, node_id: str, value: Any):
        if self.raw_inputs is not None:
            raise JiuWenBaseException(StatusCode.INTERACTIVE_UPDATE_FAILED.code,
                                      StatusCode.INTERACTIVE_UPDATE_FAILED.errmsg)
        if node_id is None or value is None:
            raise JiuWenBaseException(StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.code,
                            StatusCode.INTERACTIVE_INVALID_INPUT_ERROR.errmsg)
        self.user_inputs[node_id] = value
