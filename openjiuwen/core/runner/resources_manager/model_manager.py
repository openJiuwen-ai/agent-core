# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Optional, Tuple

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.session.tracer import decorate_model_with_trace
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.runner.resources_manager.thread_safe_dict import ThreadSafeDict


class ModelMgr:
    """
    Thread-Safe Model Manager
    """
    __slots__ = ("_models",)

    def __init__(self) -> None:
        self._models: ThreadSafeDict[str, Model] = ThreadSafeDict()

    def add_model(self, model_id: str, model: Model) -> None:
        if model_id is None:
            raise JiuWenBaseException(StatusCode.SESSION_MODEL_ADD_FAILED.code,
                                      StatusCode.SESSION_MODEL_ADD_FAILED.errmsg.format(
                                          reason="model_id is invalid, can not be None"))
        if model is None:
            raise JiuWenBaseException(StatusCode.SESSION_MODEL_ADD_FAILED.code,
                                      StatusCode.SESSION_MODEL_ADD_FAILED.errmsg.format(
                                          reason="model is invalid, can not be None"))
        self._models[model_id] = model

    def add_models(self, models: List[Tuple[str, Model]]) -> None:
        for model_id, model in models:
            self.add_model(model_id, model)

    def remove_model(self, model_id: str) -> Optional[Model]:
        if model_id is None:
            return None
        return self._models.pop(model_id, None)

    def get_model(self, model_id: str, session=None) -> Optional[Model]:
        if model_id is None:
            raise JiuWenBaseException(StatusCode.SESSION_MODEL_GET_FAILED.code,
                                      StatusCode.SESSION_MODEL_GET_FAILED.errmsg.format(
                                          reason="model_id is invalid, can not be None"))
        model = self._models.get(model_id)
        return decorate_model_with_trace(model, session)
