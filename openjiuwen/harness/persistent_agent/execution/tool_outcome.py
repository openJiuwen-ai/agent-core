# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal, message-bound completion receipts for PA-11 retention, not business success claims."""

import hashlib
import json
import re

from openjiuwen.core.foundation.llm import ToolMessage

_RECEIPT_KEY = "_persistent_tool_completion"
_TERMINAL_SUCCESS = frozenset({"ok", "success", "succeeded", "completed", "done"})
# Conservative retention hint, not shell validation: false positives keep evidence.
# Do not mistake synchronous && or file-descriptor redirects (2>&1) for background jobs.
_BACKGROUND_LAUNCH = re.compile(
    r"(?<![&>])&(?!&)|\bnohup\b|\bStart-(?:Process|Job|ThreadJob)\b"
    r"|(?:^|[;&|\n])\s*(?:cmd(?:\.exe)?\s+/c\s+)?start(?:\s|$)",
    re.IGNORECASE,
)


def _fingerprint(value) -> str | None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        return None


def _call_fingerprint(call) -> str | None:
    return _fingerprint([call.id, call.name, call.arguments])


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _terminal_success(call, result) -> bool:
    if _field(result, "success") is not True:
        return False
    try:
        arguments = json.loads(call.arguments) if isinstance(call.arguments, str) else call.arguments
    except (TypeError, ValueError):
        return False
    if not isinstance(arguments, dict) or arguments.get("run_in_background"):
        return False
    if call.name == "bash":
        command = arguments.get("command")
        if not isinstance(command, str) or _BACKGROUND_LAUNCH.search(command):
            return False
    for value in (result, _field(result, "data")):
        status = _field(value, "status")
        if status is not None and (not isinstance(status, str) or status not in _TERMINAL_SUCCESS):
            return False
        if _field(value, "pid") is not None or _field(value, "completed") is False or _field(value, "done") is False:
            return False
        if _field(value, "exit_code") not in (None, 0):
            return False
    return True


def record_tool_completion(message, call, result, exception, args_summary: str) -> None:
    """Called only after trusted dispatch; text saying 'success' is never a receipt."""
    if not isinstance(message, ToolMessage):
        return
    message.metadata = dict(message.metadata or {})
    message.metadata.pop(_RECEIPT_KEY, None)
    if call is None or message.tool_call_id != call.id or exception is not None or not _terminal_success(call, result):
        return
    call_digest, content_digest = _call_fingerprint(call), _fingerprint(message.content)
    if call_digest is None or content_digest is None:
        return
    message.metadata[_RECEIPT_KEY] = {
        "version": 1,
        "call_digest": call_digest,
        "content_digest": content_digest,
        "args_summary": args_summary[:200],
    }


def has_completed_tool_receipt(call, message) -> bool:
    """Unknown/legacy or rewritten messages remain protected; no parsing of model-visible prose."""
    if not isinstance(message, ToolMessage) or message.tool_call_id != call.id:
        return False
    receipt = (message.metadata or {}).get(_RECEIPT_KEY)
    return (
        isinstance(receipt, dict)
        and receipt.get("version") == 1
        and isinstance(receipt.get("call_digest"), str)
        and receipt["call_digest"] == _call_fingerprint(call)
        and isinstance(receipt.get("content_digest"), str)
        and receipt["content_digest"] == _fingerprint(message.content)
        and isinstance(receipt.get("args_summary"), str)
    )


def completed_tool_note(call, message) -> str:
    """A bounded, already redacted hint; the archived result remains the evidence."""
    receipt = message.metadata[_RECEIPT_KEY]
    return f"{call.name} [tool_call:{call.id}] {receipt['args_summary']}"
