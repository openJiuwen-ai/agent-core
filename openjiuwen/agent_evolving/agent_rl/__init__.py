# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RL training extension for openjiuwen (agent_rl).

This package provides:
- Data structures & config schemas for RL training
- Verl-based training executor (VerlTrainingExecutor extends RayPPOTrainer)
- MainTrainer for coordinating the training loop
- ParallelRuntimeExecutor for concurrent rollout generation
- High-level optimizer entrypoints (OfflineRLOptimizer, OnlineRLOptimizer)

Sub-packages:
- optimizer/   : User-facing entrypoints (OfflineRLOptimizer, OnlineRLOptimizer, TaskRunners)
- rl_trainer/  : PPO training core pipeline (VerlTrainingExecutor, run_ppo_step)
- offline/     : Offline RL specific modules
- online/      : Online RL (gateway, scheduler, storage, trainer; see examples/jiuwenrl_online)
"""

from openjiuwen.core.common.logging import logger


def _patch_lazy_logger():
    """Make LazyLogger.__getattr__ safe against recursion during unpickle."""
    try:
        from openjiuwen.core.common.logging import LazyLogger

        if getattr(LazyLogger.__getattr__, "agent_rl_patched", False):
            return

        def _safe_getattr(self, name):
            try:
                _logger = object.__getattribute__(self, "_logger")
            except AttributeError:
                _logger = None

            if _logger is None:
                from openjiuwen.core.common.logging import _ensure_initialized
                _ensure_initialized()
                getter = object.__getattribute__(self, "_getter_func")
                _logger = getter()
                object.__setattr__(self, "_logger", _logger)
            return getattr(_logger, name)

        _safe_getattr.agent_rl_patched = True
        LazyLogger.__getattr__ = _safe_getattr
    except Exception as e:
        logger.info("agent_rl: skip lazy_logger patch (core not available): %s", e)


_patch_lazy_logger()


def __getattr__(name):
    if name == "OfflineRLOptimizer":
        from openjiuwen.agent_evolving.agent_rl.optimizer.rl_optimizer import OfflineRLOptimizer
        return OfflineRLOptimizer
    if name == "OnlineRLOptimizer":
        from openjiuwen.agent_evolving.agent_rl.optimizer.rl_optimizer import OnlineRLOptimizer
        return OnlineRLOptimizer
    if name == "RLRail":
        from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail
        return RLRail
    if name == "RLOnlineRail":
        from openjiuwen.agent_evolving.agent_rl.online.rail import RLOnlineRail
        return RLOnlineRail
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from openjiuwen.agent_evolving.agent_rl.schemas import (
    Rollout,
    RolloutMessage,
    RLTask,
    RolloutWithReward,
)
from openjiuwen.agent_evolving.agent_rl.config.offline_config import RLConfig
from openjiuwen.agent_evolving.agent_rl.reward import RewardRegistry

__all__ = [
    "RLConfig",
    "OfflineRLOptimizer",
    "OnlineRLOptimizer",
    "RewardRegistry",
    "RLRail",
    "RLOnlineRail",
    "RLTask",
    "Rollout",
    "RolloutMessage",
    "RolloutWithReward",
]
