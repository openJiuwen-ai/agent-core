# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""记录工具显式执行结果，不改变工具返回值和异常传播。"""

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.events import EventStatus, LogEventType


def _session_value(session, name):
    try:
        getter = getattr(session, name, None)
        return getter() if callable(getter) else None
    except Exception:
        return None


def begin_execution(tool, args, kwargs):
    if not getattr(tool, "_log_execution_status", False):
        return None
    session = kwargs.get("session")
    return {"tool_name": tool.card.name, "tool_id": tool.card.id,
            "tool_call_id": kwargs.get("tool_call_id"),
            "session_id": _session_value(session, "get_session_id"),
            "agent_id": _session_value(session, "get_agent_id")}


def finish_execution(record, result=None, error=None):
    if record is None:
        return
    try:
        outcome = getattr(result, "tool_execution_status", None)
        if outcome is None:
            success = getattr(result, "success", None)
            outcome = "success" if success is True else "failure" if success is False else "unknown"
        commit_state = getattr(result, "tool_commit_state", "not_needed")
        counts = dict(getattr(result, "tool_item_counts", {}))
        if error is not None:
            details = getattr(error, "details", None)
            if isinstance(details, dict):
                commit_state = details.get("commit_state", commit_state)
            commit_state = getattr(error, "tool_commit_state", commit_state)
            counts.update(getattr(error, "tool_item_counts", {}))
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
        emit = logger.info if status == EventStatus.SUCCESS else logger.warning
        emit("[Tool] invoke %s tool=%s tool_id=%s", "success" if status == EventStatus.SUCCESS else "failed",
             record["tool_name"], record["tool_id"],
             event_type=LogEventType.TOOL_CALL_END if status == EventStatus.SUCCESS else LogEventType.TOOL_CALL_ERROR,
             status=status, tool_name=record["tool_name"], tool_call_id=record["tool_call_id"],
             session_id=record["session_id"], error_message=str(error)[:1024] if error is not None else None,
             metadata={**record, "event_role": "terminal", "outcome": outcome,
                       "commit_state": commit_state, "item_counts": counts,
                       "outer_error_code": getattr(error, "code", None),
                       "error_type": type(error).__name__ if error is not None else None})
    except Exception:
        # 日志失败不能把已提交操作变成可安全重试的假象，也不能覆盖原异常。
        pass
