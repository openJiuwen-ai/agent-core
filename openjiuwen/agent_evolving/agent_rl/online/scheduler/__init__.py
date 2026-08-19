# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Scheduler package for online RL training loop."""

from importlib import import_module

_EXPORTS = {
    "EvalRequest": "openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins",
    "EvalResult": "openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins",
    "OnlineTrainingScheduler": "openjiuwen.agent_evolving.agent_rl.online.core.scheduler",
    "PPOTrainingExecutor": "openjiuwen.agent_evolving.agent_rl.online.backends.rl.trainer",
    "RolloutRequest": "openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins",
    "RolloutResult": "openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_EXPORTS[name])
    return getattr(module, name)
