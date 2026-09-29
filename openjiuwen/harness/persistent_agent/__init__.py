# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent construction, invocation contracts and protocol values."""

from importlib import import_module

__all__ = [
    "PersistentAgentSpec",
    "PersistentBuildContext",
    "ExecutionContext",
    "PersistentPorts",
    "ContextStore",
    "HistoryPort",
    "KnowledgePort",
    "GroupMaterialPort",
    "create_persistent_agent",
    "InvocationRef",
    "ModelUsage",
    "WindowHead",
    "WindowRecord",
    "ContextHeadResult",
    "WindowReadResult",
    "WindowWrite",
    "HeadAdvance",
    "WindowSwitch",
    "WindowMutationResult",
    "MaterialQuery",
    "MaterialSnapshot",
    "MaterialReadResult",
    "AgentEvent",
    "EventType",
    "ParticipationPayload",
    "StartedPayload",
    "TextPayload",
    "ReplyDeltaPayload",
    "OutputPayload",
    "ModelCompletedPayload",
    "ToolIntentPayload",
    "ToolCompletedPayload",
    "CheckpointPayload",
    "WaitingPayload",
    "SuspendedPayload",
    "WindowPayload",
    "FinalPayload",
    "CommitResult",
    "AuthorityResult",
    "ControlCommand",
    "ControlResult",
    "BudgetAdmission",
    "BudgetReport",
]

_MODULES = {
    "PersistentAgentSpec": "schema.spec",
    "PersistentBuildContext": "schema.build_context",
    "ExecutionContext": "schema.execution",
    "PersistentPorts": "ports",
    **dict.fromkeys(("ContextStore", "HistoryPort", "KnowledgePort", "GroupMaterialPort"), "ports"),
    "create_persistent_agent": "factory",
    **dict.fromkeys(("InvocationRef", "ModelUsage"), "schema.protocol"),
    **dict.fromkeys(
        (
            "WindowHead",
            "WindowRecord",
            "ContextHeadResult",
            "WindowReadResult",
            "WindowWrite",
            "HeadAdvance",
            "WindowSwitch",
            "WindowMutationResult",
            "MaterialQuery",
            "MaterialSnapshot",
            "MaterialReadResult",
        ),
        "schema.context",
    ),
    **dict.fromkeys(
        (
            "AgentEvent",
            "EventType",
            "ParticipationPayload",
            "StartedPayload",
            "TextPayload",
            "ReplyDeltaPayload",
            "OutputPayload",
            "ModelCompletedPayload",
            "ToolIntentPayload",
            "ToolCompletedPayload",
            "CheckpointPayload",
            "WaitingPayload",
            "SuspendedPayload",
            "WindowPayload",
            "FinalPayload",
        ),
        "schema.events",
    ),
    **dict.fromkeys(
        ("CommitResult", "AuthorityResult", "ControlCommand", "ControlResult", "BudgetAdmission", "BudgetReport"),
        "schema.port_results",
    ),
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.persistent_agent.{_MODULES[name]}"), name)
    globals()[name] = value
    return value
