# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Trajectory ingestion and storage helpers for the online-RL gateway."""

from importlib import import_module

_EXPORTS = {
    "SampleRecorder": "openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_recorder",
    "JudgeDispatcher": "openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.judge_dispatcher",
    "PendingJudgeStore": "openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store",
    "GatewayTrajectoryRuntime": "openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.persistence",
    "RailBatchIngestor": "openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.rail_ingest",
    "build_sample": "openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads",
    "coerce_logprobs": "openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_EXPORTS[name])
    return getattr(module, name)
