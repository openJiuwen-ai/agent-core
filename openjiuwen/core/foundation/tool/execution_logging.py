# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""工具显式终态的可选日志协议，不改变工具结果或回调控制流。"""

import asyncio
import uuid
from contextvars import ContextVar
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.events import EventStatus, LogEventType

_EXECUTION = ContextVar("tool_execution_log", default=None)


def _session_value(session, name):
    try:
        getter = getattr(session, name, None)
        return getter() if callable(getter) else None
    except Exception:
        return None


@lru_cache(maxsize=1)
def _distribution_version():
    try:
        return version("openjiuwen")
    except PackageNotFoundError:
        return None


def begin_execution(tool, args, kwargs):
    if not getattr(tool, "_log_execution_status", False):
        return None
    session = kwargs.get("session")
    session_id = _session_value(session, "get_session_id")
    parent = _EXECUTION.get()
    call_id = kwargs.get("tool_call_id")
    internal = parent is not None and parent["tool_call_id"] == call_id and parent["session_id"] == session_id
    kind = "internal_engine" if internal else "model" if call_id else "direct"
    inputs = args[0] if args and isinstance(args[0], dict) else {}
    fields = ("action", "modify_action", "tasks", "todos", "ids", "todo_data", "id", "plan_action")
    action = inputs.get("modify_action") or inputs.get("action")
    record = {"execution_id": uuid.uuid4().hex, "tool_call_id": call_id, "session_id": session_id,
              "agent_id": _session_value(session, "get_agent_id"),
              "tool_name": tool.card.name, "tool_id": tool.card.id, "invocation_kind": kind,
              "parent_execution_id": parent["execution_id"] if internal else None,
              "action": action[:64] if isinstance(action, str) else None,
              "argument_counts": {key: len(inputs[key]) for key in fields
                                  if key in inputs and isinstance(inputs[key], list)},
              "argument_types": {key: type(inputs[key]).__name__ for key in fields if key in inputs}}
    return _EXECUTION.set(record)


def finish_execution(token, result=None, error=None):
    if token is None:
        return
    record = _EXECUTION.get()
    try:
        _emit_terminal(record, result, error)
    except Exception:
        # 日志不可用不能撤销已确认提交，也不能制造可安全重试的假象。
        pass
    finally:
        _EXECUTION.reset(token)


def _emit_terminal(record, result, error):
    outcome = getattr(result, "tool_execution_status", None)
    if outcome is None:
        success = getattr(result, "success", None)
        outcome = "success" if success is True else "failure" if success is False else "unknown"
    commit_state = getattr(result, "tool_commit_state", "not_needed")
    counts = dict(getattr(result, "tool_item_counts", {}))
    outer_code = getattr(error, "code", None)
    root_code = reason = None
    error_context = {}
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "code", None)
        if code is not None:
            root_code = code
        commit_state = getattr(current, "tool_commit_state", commit_state)
        error_counts = getattr(current, "tool_item_counts", None)
        if isinstance(error_counts, dict):
            counts.update(error_counts)
        details = getattr(current, "details", None)
        if isinstance(details, dict):
            reason = details.get("reason", reason)
            commit_state = details.get("commit_state", commit_state)
            error_context.update({key: details[key] for key in ("field", "item_index") if key in details})
        current = getattr(current, "cause", None) or getattr(current, "__cause__", None)
    if error is not None:
        outcome = "unknown" if commit_state == "unknown" else "failure"
        if isinstance(error, asyncio.CancelledError):
            outcome = "cancelled"
        elif isinstance(error, TimeoutError):
            outcome = "timeout"
        if commit_state == "unknown":
            counts["applied_count"] = None
        elif commit_state != "committed":
            counts["applied_count"] = 0
    status = {"success": EventStatus.SUCCESS, "noop": EventStatus.SUCCESS,
              "timeout": EventStatus.TIMEOUT, "cancelled": EventStatus.CANCELLED}.get(outcome, EventStatus.FAILURE)
    metadata = {**record, "event_role": "terminal", "outcome": outcome,
                "event_schema": "tool-execution.v1",
                "distribution_version": _distribution_version(),
                "commit_state": commit_state, "item_counts": counts,
                "outer_error_code": outer_code, "root_error_code": root_code, "reason": reason,
                "error_context": error_context, "error_type": type(error).__name__ if error is not None else None}
    snapshot = getattr(result, "todo_snapshot", None)
    if isinstance(snapshot, dict):
        metadata.update(plan_id=snapshot.get("plan_id"), revision=snapshot.get("revision"))
    emit = logger.info if status == EventStatus.SUCCESS else logger.warning
    emit("[Tool] invoke %s tool=%s tool_id=%s", "success" if status == EventStatus.SUCCESS else "failed",
         record["tool_name"], record["tool_id"],
         event_type=LogEventType.TOOL_CALL_END if status == EventStatus.SUCCESS else LogEventType.TOOL_CALL_ERROR,
         status=status, tool_name=record["tool_name"], tool_call_id=record["tool_call_id"],
         session_id=record["session_id"], error_message=str(error)[:1024] if error is not None else None,
         metadata=metadata)
