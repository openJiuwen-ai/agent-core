# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Environment adapter registry."""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Dict, Type

from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter
from openjiuwen.core.common.logging import logger

_ENV_REGISTRY: Dict[str, Type[EnvAdapter]] = {}


def _try_register(name: str, import_path: str, class_name: str) -> None:
    if name in _ENV_REGISTRY:
        return
    try:
        module = importlib.import_module(import_path)
        adapter_cls = getattr(module, class_name)
        _ENV_REGISTRY[name] = adapter_cls
    except ImportError as exc:
        logger.warning(
            "Skipping env adapter %r (%s.%s): %s",
            name,
            import_path,
            class_name,
            exc,
        )


def _register_builtins() -> None:
    if _ENV_REGISTRY:
        return
    _try_register(
        "searchqa",
        "openjiuwen.agent_evolving.skill_train.envs.searchqa.adapter",
        "SearchQAAdapter",
    )
    _try_register(
        "docvqa",
        "openjiuwen.agent_evolving.skill_train.envs.docvqa.adapter",
        "DocVQAAdapter",
    )
    _try_register(
        "officeqa",
        "openjiuwen.agent_evolving.skill_train.envs.officeqa.adapter",
        "OfficeQAAdapter",
    )


def get_env_adapter(env_name: str, **kwargs: Any) -> EnvAdapter:
    """Instantiate an environment adapter by name."""
    _register_builtins()
    if env_name not in _ENV_REGISTRY:
        raise ValueError(f"Unknown environment '{env_name}'. Available: {list(_ENV_REGISTRY.keys())}")
    adapter_cls = _ENV_REGISTRY[env_name]
    sig = inspect.signature(adapter_cls.__init__)
    params = sig.parameters
    # ``def __init__(self, **kwargs)`` exposes only the name "kwargs"; pass through.
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return adapter_cls(**kwargs)
    accepted = {
        name
        for name, param in params.items()
        if name != "self" and param.kind != inspect.Parameter.VAR_POSITIONAL
    }
    adapter_kwargs = {key: value for key, value in kwargs.items() if key in accepted}
    return adapter_cls(**adapter_kwargs)


def list_env_adapters() -> list[str]:
    _register_builtins()
    return sorted(_ENV_REGISTRY.keys())
