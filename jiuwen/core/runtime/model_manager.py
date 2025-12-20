#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Optional, Tuple

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.tracer.decorator import decrate_model_with_trace
from jiuwen.core.utils.llm.base import BaseChatModel
from jiuwen.core.runtime.thread_safe_dict import ThreadSafeDict


class ModelMgr:
    """
    Thread-Safe Model Manager
    """
    __slots__ = ("_models",)

    def __init__(self) -> None:
        self._models: ThreadSafeDict[str, BaseChatModel] = ThreadSafeDict()

    def add_model(self, model_id: str, model: BaseChatModel) -> None:
        if model_id is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_MODEL_ADD_FAILED.code,
                                      StatusCode.RUNTIME_MODEL_ADD_FAILED.errmsg.format(
                                          reason="model_id is invalid, can not be None"))
        if model is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_MODEL_ADD_FAILED.code,
                                      StatusCode.RUNTIME_MODEL_ADD_FAILED.errmsg.format(
                                          reason="model is invalid, can not be None"))
        self._models[model_id] = model

    def add_models(self, models: List[Tuple[str, BaseChatModel]]) -> None:
        for model_id, model in models:
            self.add_model(model_id, model)

    def remove_model(self, model_id: str) -> Optional[BaseChatModel]:
        if model_id is None:
            return None
        return self._models.pop(model_id, None)

    def get_model(self, model_id: str, runtime=None) -> Optional[BaseChatModel]:
        if model_id is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_MODEL_GET_FAILED.code,
                                      StatusCode.RUNTIME_MODEL_GET_FAILED.errmsg.format(
                                          reason="model_id is invalid, can not be None"))
        model = self._models.get(model_id)
        return decrate_model_with_trace(model, runtime)
