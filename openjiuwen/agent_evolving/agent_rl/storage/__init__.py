# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared RL/SFT sample storage interfaces and implementations."""

from importlib import import_module

_EXPORTS = {
    "InMemoryTrajectoryStore": "openjiuwen.agent_evolving.agent_rl.online.backends.rl.store",
    "TrajectorySampleStore": "openjiuwen.agent_evolving.agent_rl.online.backends.rl.store",
    "InMemorySFTStore": "openjiuwen.agent_evolving.agent_rl.online.backends.sft.store",
    "SFTSampleStore": "openjiuwen.agent_evolving.agent_rl.online.backends.sft.store",
    "LoRARepository": "openjiuwen.agent_evolving.agent_rl.storage.lora_repo",
    "LoRAVersion": "openjiuwen.agent_evolving.agent_rl.storage.lora_repo",
    "LocalPendingJudgeStore": "openjiuwen.agent_evolving.agent_rl.storage.local_store",
    "LocalSFTStore": "openjiuwen.agent_evolving.agent_rl.storage.local_store",
    "LocalTrajectoryStore": "openjiuwen.agent_evolving.agent_rl.storage.local_store",
    "LocalTrainingTaskStore": "openjiuwen.agent_evolving.agent_rl.storage.local_store",
    "RedisTrajectoryStore": "openjiuwen.agent_evolving.agent_rl.online.backends.rl.redis_store",
    "RedisSFTStore": "openjiuwen.agent_evolving.agent_rl.online.backends.sft.redis_store",
    "TrainingTaskStore": "openjiuwen.agent_evolving.agent_rl.storage.training_task_store",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_EXPORTS[name])
    return getattr(module, name)
