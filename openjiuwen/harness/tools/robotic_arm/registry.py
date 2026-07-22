# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Name-keyed registry for step_executor (camera + arm/algorithm) vendor adapters.

Mirrors ``SandboxRegistry`` (openjiuwen/core/sys_operation/sandbox/sandbox_registry.py):
a concrete vendor adapter class registers itself under a short model name via a
decorator; callers then select a model by name instead of importing and
constructing the vendor class directly. Passing an already-constructed
``step_executor=`` object on :class:`RoboticArmRuntimeSettings` remains the
escape hatch for one-off or unregistered rigs.
"""

from __future__ import annotations

from typing import Any, Dict, Type


class SubTaskExecutorRegistry:
    """Registry mapping a rig name to its ``SubTaskExecutor`` implementation class."""

    _registry: Dict[str, Type] = {}

    @classmethod
    def register(cls, model_name: str):
        def decorator(executor_cls: Type) -> Type:
            cls._registry[model_name] = executor_cls
            return executor_cls

        return decorator

    @classmethod
    def create(cls, model_name: str, **kwargs: Any) -> Any:
        executor_cls = cls._registry.get(model_name)
        if executor_cls is None:
            raise ValueError(f"Unknown step_executor model {model_name!r}. Registered models: {sorted(cls._registry)}")
        return executor_cls(**kwargs)


__all__ = ["SubTaskExecutorRegistry"]
