#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Any

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.runtime.runtime import BaseRuntime
from jiuwen.core.runtime.workflow_state import CommitState


class AtomicNode(ABC):
    def atomic_invoke(self, **kwargs) -> Any:
        runtime = kwargs.get("runtime", None)
        if runtime is None or not isinstance(runtime, BaseRuntime):
            raise JiuWenBaseException(-1, "failed to get runtime")
        if not isinstance(runtime.state(), CommitState):
            raise JiuWenBaseException(-1, "state type error, not commit state")
        result = self._atomic_invoke(**kwargs)
        runtime.state().commit_cmp()
        return result

    @abstractmethod
    def _atomic_invoke(self, **kwargs) -> Any:
        pass


class AsyncAtomicNode(ABC):
    async def atomic_invoke(self, **kwargs) -> Any:
        runtime = kwargs.get("runtime", None)
        if runtime is None or not isinstance(runtime, BaseRuntime):
            raise JiuWenBaseException(-1, "failed to get runtime")
        if not isinstance(runtime.state(), CommitState):
            raise JiuWenBaseException(-1, "state type error, not commit state")
        result = await self._atomic_invoke(**kwargs)
        runtime.state().commit_cmp()
        return result

    @abstractmethod
    async def _atomic_invoke(self, **kwargs) -> Any:
        pass
