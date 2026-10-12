# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent construction, invocation contracts and protocol values."""

from importlib import import_module

__all__ = [
    "PersistentAgentSpec",
    "PersistentBuildContext",
    "ExecutionContext",
    "PersistentPorts",
    "KnowledgePort",
    "GroupChatPort",
    "WorkspaceAccessPort",
    "WorkspaceReadScope",
    "WorkspaceWriteAccessPort",
    "WorkspaceWriteScope",
    "create_persistent_agent",
    "execute_persistent_request",
    "resume_persistent_request",
    "abort_persistent_request",
    "RequestInput",
    "DirectMessageSource",
    "ConfirmReply",
    "PermissionResponse",
    "AnswerResponse",
    "InvocationRef",
    "ModelUsage",
    "GroupMessage",
    "GroupMessageQuery",
    "GroupMessagesResult",
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
    "WindowSwitchedPayload",
    "InterceptedToolCall",
    "PausedPayload",
    "FinalPayload",
    "BudgetAdmission",
    "BudgetReport",
]

_MODULES = {
    "PersistentAgentSpec": "schema.spec",
    "PersistentBuildContext": "schema.build_context",
    "ExecutionContext": "schema.execution",
    "PersistentPorts": "ports",
    **dict.fromkeys(("KnowledgePort", "GroupChatPort"), "ports"),
    **dict.fromkeys(("WorkspaceAccessPort", "WorkspaceReadScope"), "workspace.access"),
    **dict.fromkeys(("WorkspaceWriteAccessPort", "WorkspaceWriteScope"), "workspace.access"),
    "create_persistent_agent": "factory",
    **dict.fromkeys(
        ("execute_persistent_request", "resume_persistent_request", "abort_persistent_request"), "execution.entries"
    ),
    **dict.fromkeys(
        ("RequestInput", "DirectMessageSource", "ConfirmReply", "PermissionResponse", "AnswerResponse"),
        "schema.request",
    ),
    **dict.fromkeys(("InvocationRef", "ModelUsage"), "schema.protocol"),
    **dict.fromkeys(
        (
            "GroupMessage",
            "GroupMessageQuery",
            "GroupMessagesResult",
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
            "WindowSwitchedPayload",
            "InterceptedToolCall",
            "PausedPayload",
            "FinalPayload",
        ),
        "schema.events",
    ),
    **dict.fromkeys(("BudgetAdmission", "BudgetReport"), "schema.port_results"),
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.persistent_agent.{_MODULES[name]}"), name)
    globals()[name] = value
    return value
