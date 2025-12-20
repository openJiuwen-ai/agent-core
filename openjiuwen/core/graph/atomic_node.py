#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Any, Optional

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.runtime.workflow_state import CommitState


def _validate_runtime_and_state(runtime: Optional[BaseRuntime]) -> None:
    if runtime is None:
        raise JiuWenBaseException(StatusCode.RUNTIME_STATE_RUNTIME_NONE.code, 
                                 StatusCode.RUNTIME_STATE_RUNTIME_NONE.errmsg)
    
    if not isinstance(runtime, BaseRuntime):
        raise JiuWenBaseException(StatusCode.RUNTIME_STATE_INVALID_RUNTIME_TYPE.code, 
                                 StatusCode.RUNTIME_STATE_INVALID_RUNTIME_TYPE.errmsg.format(
                                     runtime_type=type(runtime).__name__))
    
    state = runtime.state()
    if not isinstance(state, CommitState):
        raise JiuWenBaseException(StatusCode.RUNTIME_STATE_INVALID_STATE_TYPE.code, 
                                 StatusCode.RUNTIME_STATE_INVALID_STATE_TYPE.errmsg.format(
                                     state_type=type(state).__name__))


class AtomicNode(ABC):
    def atomic_invoke(self, **kwargs) -> Any:
        runtime = kwargs.get("runtime", None)
        _validate_runtime_and_state(runtime)
        result = self._atomic_invoke(**kwargs)
        runtime.state().commit_cmp()
        return result

    @abstractmethod
    def _atomic_invoke(self, **kwargs) -> Any:
        pass


class AsyncAtomicNode(ABC):
    async def atomic_invoke(self, **kwargs) -> Any:
        runtime = kwargs.get("runtime", None)
        _validate_runtime_and_state(runtime)
        result = await self._atomic_invoke(**kwargs)
        runtime.state().commit_cmp()
        return result

    @abstractmethod
    async def _atomic_invoke(self, **kwargs) -> Any:
        pass
