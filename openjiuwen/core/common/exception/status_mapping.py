# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025.

from __future__ import annotations

from typing import (
    Dict,
    Type,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import (
    AgentError,
    BaseError,
    ExecutionError,
    FrameworkError,
    GraphError,
    SessionError,
    Termination,
    ToolError,
    ValidationError,
    WorkflowError,
    ToolchainError,
    ContextError,
    RunnerError,
)

_EXCEPTION_CLASS_REGISTRY: Dict[str, Type[BaseError]] = {
    "BaseError": BaseError,
    "FrameworkError": FrameworkError,
    "ExecutionError": ExecutionError,
    "ValidationError": ValidationError,
    "Termination": Termination,
    "WorkflowError": WorkflowError,
    "AgentError": AgentError,
    "ToolError": ToolError,
    "GraphError": GraphError,
    "SessionError": SessionError,
    "ToolchainError": ToolchainError,
    "ContextError": ContextError,
    "RunnerError": RunnerError,
}

KEYWORD_RULES = [
    (("INVALID", "NOT_SUPPORTED", "PARAM", "MISSING", "DUPLICATED"), "ValidationError"),
    (("CONFIG", "SCHEMA", "FORMAT", "TEMPLATE"), "ValidationError"),

    (("INIT", "CONNECT", "SERVICE", "QUEUE", "PROVIDER"), "FrameworkError"),
    (("CALL", "INVOKE_LLM", "MODEL", "REMOTE"), "FrameworkError"),

    (("TIMEOUT", "EXECUTE", "EXECUTION", "RUNTIME", "PROCESS", "STREAM"), "ExecutionError"),
]

RANGE_RULES = [
    ((100000, 119999), "WorkflowError"),
    ((120000, 129999), "AgentError"),
    ((130000, 139999), "RunnerError"),
    ((140000, 149999), "GraphError"),
    ((150000, 159999), "ContextError"),
    ((160000, 179999), "ToolchainError"),
    ((180000, 189999), "FrameworkError"),
    ((190000, 199999), "SessionError"),
]

MANUAL_OVERRIDES = {
    StatusCode.CONTROLLER_INVOKE_LLM_FAILED: "FrameworkError",
    StatusCode.TOOL_EXECUTION_ERROR: "ToolError",
    StatusCode.TOOL_NOT_FOUND_ERROR: "ValidationError",
    StatusCode.AGENT_GROUP_EXECUTION_ERROR: "AgentError",
}


def _match_keyword(name: str) -> str | None:
    for keywords, exc_name in KEYWORD_RULES:
        if any(k in name for k in keywords):
            return exc_name
    return None


def _match_range(code: int) -> str | None:
    for (start, end), exc_name in RANGE_RULES:
        if start <= code <= end:
            return exc_name
    return None


def resolve_exception_class(status: StatusCode) -> Type[BaseError]:
    # 1. Manual override
    if status in MANUAL_OVERRIDES:
        return _EXCEPTION_CLASS_REGISTRY[MANUAL_OVERRIDES[status]]

    name = status.name
    code = status.code

    # 2. Keyword rule
    exc_name = _match_keyword(name)
    if exc_name:
        return _EXCEPTION_CLASS_REGISTRY[exc_name]

    # 3. Range fallback
    exc_name = _match_range(code)
    if exc_name:
        return _EXCEPTION_CLASS_REGISTRY[exc_name]

    # 4. Absolute fallback
    return ExecutionError


def build_status_exception_map() -> Dict[StatusCode, Type[BaseError]]:
    """
    Generate full StatusCode -> ExceptionClass mapping.
    """
    mapping: Dict[StatusCode, Type[BaseError]] = {}
    for status in StatusCode:
        mapping[status] = resolve_exception_class(status)
    return mapping
