# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Environment adapter registry."""

from __future__ import annotations

import inspect
from typing import Any, Dict, Type

from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter

_ENV_REGISTRY: Dict[str, Type[EnvAdapter]] = {}


def _try_register(name: str, import_path: str, class_name: str) -> None:
    if name in _ENV_REGISTRY:
        return
    try:
        module = __import__(import_path, fromlist=[class_name])
        adapter_cls = getattr(module, class_name)
        _ENV_REGISTRY[name] = adapter_cls
    except ImportError:
        pass


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
        "alfworld",
        "openjiuwen.agent_evolving.skill_train.envs.alfworld.adapter",
        "ALFWorldAdapter",
    )
    _try_register(
        "officeqa",
        "openjiuwen.agent_evolving.skill_train.envs.officeqa.adapter",
        "OfficeQAAdapter",
    )
    _try_register(
        "spreadsheetbench",
        "openjiuwen.agent_evolving.skill_train.envs.spreadsheetbench.adapter",
        "SpreadsheetBenchAdapter",
    )
    _try_register(
        "livemathematicianbench",
        "openjiuwen.agent_evolving.skill_train.envs.livemathematicianbench.adapter",
        "LiveMathematicianBenchAdapter",
    )


def get_env_adapter(env_name: str, **kwargs: Any) -> EnvAdapter:
    """Instantiate an environment adapter by name."""
    _register_builtins()
    if env_name not in _ENV_REGISTRY:
        raise ValueError(
            f"Unknown environment '{env_name}'. Available: {list(_ENV_REGISTRY.keys())}"
        )
    adapter_cls = _ENV_REGISTRY[env_name]
    sig = inspect.signature(adapter_cls.__init__)
    accepted = set(sig.parameters.keys()) - {"self"}
    adapter_kwargs = {key: value for key, value in kwargs.items() if key in accepted}
    return adapter_cls(**adapter_kwargs)


def list_env_adapters() -> list[str]:
    _register_builtins()
    return sorted(_ENV_REGISTRY.keys())
