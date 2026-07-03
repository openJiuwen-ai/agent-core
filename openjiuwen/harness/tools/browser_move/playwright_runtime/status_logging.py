# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import (
    browser_agent_log_info,
)

_STATUS_KEY = "_browser_subagent_status_logging"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_TEXT_VALUE_KEYS = {
    "value",
    "text",
    "query",
    "name",
    "email",
    "phone",
    "mobile",
    "password",
    "address",
    "code",
    "script",
    "js",
}
_BATCH_VALUE_KEYS = {
    "value",
    "text",
    "choose_text",
    "option_text",
    "option_label",
    "option_value",
    "label_value",
    "option_name",
    "values",
}
_BATCH_TARGET_KEYS = {
    "selector",
    "label",
    "placeholder",
    "role",
    "name",
    "aria_label",
    "testid",
    "text",
    "option_role",
    "option_selector",
    "year_selector",
    "month_selector",
    "day_selector",
}
_TOOL_HISTORY_LIMIT = 20
_URL_RE = re.compile(r"https?://[^\s)>'\"]+", re.IGNORECASE)

MetadataProvider = Callable[[], Mapping[str, Any]]


def is_browser_subagent_status_log_enabled() -> bool:
    """Return whether browser subagent status logging is enabled."""
    raw = (os.getenv("BROWSER_SUBAGENT_STATUS_LOG") or "").strip().lower()
    if raw in _FALSE_VALUES:
        return False
    if raw in _TRUE_VALUES:
        return True
    return True


def _safe_str(value: Any, limit: int = 240) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def _mapping_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _redacted_text(value: Any) -> dict[str, Any]:
    text = "" if value is None else str(value)
    return {
        "kind": type(value).__name__,
        "length": len(text),
        "redacted": True,
    }


def _sanitize_url(value: Any) -> str:
    raw = _safe_str(value, 600)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return _safe_str(raw.split("?")[0], 180)
    if not parts.scheme and not parts.netloc:
        return _safe_str(raw.split("?")[0], 180)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>" if parts.query else "", ""))


def _extract_text_payload(value: Any) -> Any:
    """Unwrap common ToolOutput/MCP text result shapes."""
    if hasattr(value, "success") and hasattr(value, "data"):
        data = getattr(value, "data", None)
        error = getattr(value, "error", None)
        if data is not None:
            return data
        if error:
            return {"ok": False, "error": str(error)}
    if isinstance(value, Mapping):
        if "data" in value and isinstance(value.get("data"), (dict, list)):
            return value.get("data")
        if "result" in value:
            return value.get("result")
        if "text" in value:
            return value.get("text")
        content = value.get("content")
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, Mapping) and item.get("type") == "text":
                    texts.append(str(item.get("text") or ""))
            if texts:
                return "\n".join(texts)
    return value


class BrowserSubagentStatusLogger:
    """Emit compact structured backend logs for nested browser subagent activity."""

    def __init__(
        self,
        *,
        logger: Optional[logging.Logger] = None,
        metadata_provider: Optional[MetadataProvider] = None,
        marker: str = "[BROWSER_SUBAGENT]",
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._metadata_provider = metadata_provider
        self._marker = marker
        self._runs: dict[str, dict[str, Any]] = {}

    def before_invoke(self, ctx: Any) -> None:
        key = self._run_key(ctx)
        state = self._runs.setdefault(key, {})
        state.clear()
        state.update(
            {
                "started_at": time.monotonic(),
                "model_calls": 0,
                "tool_calls": 0,
                "tool_starts": {},
                "tool_counts": Counter(),
                "total_model_elapsed_ms": 0,
                "total_tool_elapsed_ms": 0,
                "batch_calls": 0,
                "batch_steps_total": 0,
                "batch_steps_ok": 0,
                "batch_steps_failed": 0,
                "fallback_count": 0,
                "history": [],
            }
        )
        self._remember_key(ctx, key)
        inputs = getattr(ctx, "inputs", None)
        query = _mapping_get(inputs, "query", "")
        conversation_id = _mapping_get(inputs, "conversation_id", "")
        self._emit(
            "task_start",
            ctx,
            {
                "conversation_id": _safe_str(conversation_id, 160),
                "query_summary": self._summarize_query(query),
            },
        )

    def after_invoke(self, ctx: Any) -> None:
        state = self._state(ctx)
        inputs = getattr(ctx, "inputs", None)
        result = _mapping_get(inputs, "result", None)
        elapsed_ms = self._elapsed_ms(state.get("started_at"))
        tool_counts = state.get("tool_counts") or {}
        if isinstance(tool_counts, Counter):
            tool_counts_payload = dict(sorted(tool_counts.items()))
        else:
            tool_counts_payload = dict(tool_counts)
        self._emit(
            "task_end",
            ctx,
            {
                "elapsed_ms": elapsed_ms,
                "model_calls": state.get("model_calls", 0),
                "tool_calls": state.get("tool_calls", 0),
                "tool_counts": tool_counts_payload,
                "browser_batch_calls": state.get("batch_calls", 0),
                "browser_batch_steps_total": state.get("batch_steps_total", 0),
                "browser_batch_steps_ok": state.get("batch_steps_ok", 0),
                "browser_batch_steps_failed": state.get("batch_steps_failed", 0),
                "fallback_count": state.get("fallback_count", 0),
                "total_model_elapsed_ms": state.get("total_model_elapsed_ms", 0),
                "total_tool_elapsed_ms": state.get("total_tool_elapsed_ms", 0),
                "recent_tools": list(state.get("history") or [])[-_TOOL_HISTORY_LIMIT:],
                "result_summary": self.summarize_result("browser_task", result),
            },
        )

    def before_model_call(self, ctx: Any) -> None:
        state = self._state(ctx)
        state["model_calls"] = int(state.get("model_calls", 0)) + 1
        state["model_started_at"] = time.monotonic()
        inputs = getattr(ctx, "inputs", None)
        messages = _mapping_get(inputs, "messages", []) or []
        tools = _mapping_get(inputs, "tools", []) or []
        self._emit(
            "model_start",
            ctx,
            {
                "iteration": state["model_calls"],
                "message_count": len(messages) if isinstance(messages, list) else None,
                "tool_count": len(tools) if isinstance(tools, list) else None,
            },
        )

    def after_model_call(self, ctx: Any) -> None:
        state = self._state(ctx)
        inputs = getattr(ctx, "inputs", None)
        response = _mapping_get(inputs, "response", None)
        elapsed_ms = self._elapsed_ms(state.get("model_started_at"))
        if elapsed_ms is not None:
            state["total_model_elapsed_ms"] = int(state.get("total_model_elapsed_ms", 0)) + elapsed_ms
        self._emit(
            "model_end",
            ctx,
            {
                "iteration": state.get("model_calls", 0),
                "elapsed_ms": elapsed_ms,
                "response_summary": self._summarize_model_response(response),
            },
        )

    def on_model_exception(self, ctx: Any) -> None:
        state = self._state(ctx)
        elapsed_ms = self._elapsed_ms(state.get("model_started_at"))
        if elapsed_ms is not None:
            state["total_model_elapsed_ms"] = int(state.get("total_model_elapsed_ms", 0)) + elapsed_ms
        exc = getattr(ctx, "exception", None)
        self._emit(
            "model_exception",
            ctx,
            {
                "iteration": state.get("model_calls", 0),
                "elapsed_ms": elapsed_ms,
                "error_type": type(exc).__name__ if exc is not None else "",
                "error": _safe_str(exc, 300) if exc is not None else "",
            },
        )

    def before_tool_call(self, ctx: Any) -> None:
        state = self._state(ctx)
        state["tool_calls"] = int(state.get("tool_calls", 0)) + 1
        inputs = getattr(ctx, "inputs", None)
        tool_call = _mapping_get(inputs, "tool_call")
        tool_name = _mapping_get(inputs, "tool_name", "") or self._tool_name_from_call(tool_call)
        tool_name = str(tool_name or "")
        tool_key = self._tool_key(inputs)
        state.setdefault("tool_starts", {})[tool_key] = time.monotonic()
        state.setdefault("tool_counts", Counter())[tool_name] += 1
        tool_args = _mapping_get(inputs, "tool_args", None)

        previous_batch = state.get("last_failed_batch")
        if previous_batch and tool_name and tool_name != "browser_batch_interact":
            state["fallback_count"] = int(state.get("fallback_count", 0)) + 1
            self._emit(
                "fallback_detected",
                ctx,
                {
                    "from_tool": "browser_batch_interact",
                    "to_tool": _safe_str(tool_name, 180),
                    "previous_batch": previous_batch,
                },
            )
            state["last_failed_batch"] = None

        self._emit(
            "tool_start",
            ctx,
            {
                "iteration": state.get("model_calls", 0),
                "tool_index": state.get("tool_calls", 0),
                "tool_name": _safe_str(tool_name, 180),
                "args_summary": self.summarize_args(str(tool_name), tool_args),
            },
        )

    def after_tool_call(self, ctx: Any) -> None:
        state = self._state(ctx)
        inputs = getattr(ctx, "inputs", None)
        tool_call = _mapping_get(inputs, "tool_call")
        tool_name = _mapping_get(inputs, "tool_name", "") or self._tool_name_from_call(tool_call)
        tool_name = str(tool_name or "")
        tool_key = self._tool_key(inputs)
        started_at = state.setdefault("tool_starts", {}).pop(tool_key, None)
        elapsed_ms = self._elapsed_ms(started_at)
        if elapsed_ms is not None:
            state["total_tool_elapsed_ms"] = int(state.get("total_tool_elapsed_ms", 0)) + elapsed_ms
        tool_result = _mapping_get(inputs, "tool_result", None)
        result_summary = self.summarize_result(str(tool_name), tool_result)
        self._record_tool_history(state, tool_name, elapsed_ms, result_summary)
        if tool_name == "browser_batch_interact":
            self._accumulate_batch_result(state, result_summary)
        self._emit(
            "tool_end",
            ctx,
            {
                "iteration": state.get("model_calls", 0),
                "tool_name": _safe_str(tool_name, 180),
                "elapsed_ms": elapsed_ms,
                "result_summary": result_summary,
            },
        )

    def on_tool_exception(self, ctx: Any) -> None:
        state = self._state(ctx)
        inputs = getattr(ctx, "inputs", None)
        tool_call = _mapping_get(inputs, "tool_call")
        tool_name = _mapping_get(inputs, "tool_name", "") or self._tool_name_from_call(tool_call)
        tool_key = self._tool_key(inputs)
        started_at = state.setdefault("tool_starts", {}).pop(tool_key, None)
        elapsed_ms = self._elapsed_ms(started_at)
        if elapsed_ms is not None:
            state["total_tool_elapsed_ms"] = int(state.get("total_tool_elapsed_ms", 0)) + elapsed_ms
        exc = getattr(ctx, "exception", None)
        self._emit(
            "tool_exception",
            ctx,
            {
                "iteration": state.get("model_calls", 0),
                "tool_name": _safe_str(tool_name, 180),
                "elapsed_ms": elapsed_ms,
                "error_type": type(exc).__name__ if exc is not None else "",
                "error": _safe_str(exc, 300) if exc is not None else "",
            },
        )

    def summarize_args(self, tool_name: str, tool_args: Any) -> dict[str, Any]:
        parsed = _parse_jsonish(tool_args)
        if isinstance(parsed, list):
            return {"kind": "list", "length": len(parsed)}
        if not isinstance(parsed, Mapping):
            return {"kind": type(parsed).__name__, "repr_length": len(str(parsed))}

        lowered_name = (tool_name or "").lower()
        if lowered_name == "browser_batch_interact":
            return self._summarize_batch_args(parsed)
        if "run_code" in lowered_name or "evaluate" in lowered_name:
            code = parsed.get("code") or parsed.get("script") or parsed.get("expression") or parsed.get("function")
            return {
                "kind": "code_execution",
                "keys": sorted(str(key) for key in parsed.keys()),
                "code_length": len(str(code or "")),
                "code_redacted": True,
            }
        if "navigate" in lowered_name:
            return {
                "kind": "navigation",
                "url": _sanitize_url(parsed.get("url") or parsed.get("href") or parsed.get("target")),
                "keys": sorted(str(key) for key in parsed.keys()),
            }

        summary: dict[str, Any] = {"kind": "dict", "keys": sorted(str(key) for key in parsed.keys())}
        redacted: dict[str, Any] = {}
        safe_values: dict[str, Any] = {}
        for key, value in parsed.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower in _TEXT_VALUE_KEYS or any(token in key_lower for token in ("password", "token", "secret")):
                redacted[key_str] = _redacted_text(value)
            elif key_lower in {
                "selector", "role", "label", "placeholder", "checked", "timeout", "timeout_ms", "max_items"
            }:
                safe_values[key_str] = value if isinstance(value, (bool, int, float)) else _safe_str(value, 120)
        if safe_values:
            summary["safe_values"] = safe_values
        if redacted:
            summary["redacted_values"] = redacted
        return summary

    def summarize_result(self, tool_name: str, tool_result: Any) -> dict[str, Any]:
        payload = _extract_text_payload(tool_result)
        parsed = _parse_jsonish(payload)
        if isinstance(parsed, str):
            parsed_json = _parse_jsonish(parsed)
            if not isinstance(parsed_json, str):
                parsed = parsed_json
        lowered_name = (tool_name or "").lower()
        if isinstance(parsed, Mapping):
            if lowered_name == "browser_batch_interact":
                return self._summarize_batch_result(parsed)
            return self._summarize_mapping_result(parsed, tool_name=lowered_name)
        if isinstance(parsed, list):
            return {"kind": "list", "length": len(parsed)}
        text = "" if parsed is None else str(parsed)
        return {
            "kind": type(parsed).__name__,
            "length": len(text),
            "preview": _safe_str(text, 180) if len(text) <= 180 else "<redacted_large_text>",
        }

    @staticmethod
    def _summarize_batch_args(args: Mapping[str, Any]) -> dict[str, Any]:
        steps = args.get("steps")
        if not isinstance(steps, list):
            return {"kind": "browser_batch_interact", "step_count": 0, "keys": sorted(str(key) for key in args.keys())}
        op_counts = Counter(str(step.get("op") or "<missing>") for step in steps if isinstance(step, Mapping))
        step_summaries = []
        for idx, step in enumerate(steps[:10]):
            if not isinstance(step, Mapping):
                step_summaries.append({"index": idx, "kind": type(step).__name__})
                continue
            keys = {str(key) for key in step.keys()}
            step_summaries.append(
                {
                    "index": idx,
                    "op": _safe_str(step.get("op") or "", 80),
                    "target_keys": sorted(keys & _BATCH_TARGET_KEYS),
                    "value_keys_redacted": sorted(keys & _BATCH_VALUE_KEYS),
                }
            )
        return {
            "kind": "browser_batch_interact",
            "step_count": len(steps),
            "op_counts": dict(sorted(op_counts.items())),
            "steps_preview": step_summaries,
            "truncated": len(steps) > len(step_summaries),
            "continue_on_error": bool(args.get("continue_on_error", False)),
            "timeout_ms": args.get("timeout_ms"),
            "global_timeout_ms": args.get("global_timeout_ms"),
        }

    @staticmethod
    def _summarize_batch_result(result: Mapping[str, Any]) -> dict[str, Any]:
        steps = result.get("steps") if isinstance(result.get("steps"), list) else []
        ok_steps = [step for step in steps if isinstance(step, Mapping) and bool(step.get("ok", False))]
        failed_steps = [step for step in steps if isinstance(step, Mapping) and not bool(step.get("ok", False))]
        steps_failed = len(failed_steps)
        ok = bool(result.get("ok", False))
        error = result.get("error")
        has_error = bool(error)
        had_step_errors = (not ok) or has_error or steps_failed > 0
        summary = {
            "kind": "browser_batch_interact_result",
            "ok": ok,
            "all_steps_ok": ok and not has_error and steps_failed == 0,
            "had_step_errors": had_step_errors,
            "steps_total": len(steps),
            "steps_ok": len(ok_steps),
            "steps_failed": steps_failed,
            "elapsed_ms": result.get("elapsed_ms"),
            "error": _safe_str(error, 240) if error else None,
        }
        if failed_steps:
            first = failed_steps[0]
            summary["first_failed_step"] = {
                "index": first.get("index"),
                "op": _safe_str(first.get("op") or "", 80),
                "error": _safe_str(first.get("error") or "", 240),
            }
        elif had_step_errors:
            summary["first_failed_step"] = {
                "index": None,
                "op": "",
                "error": _safe_str(error or "batch_call_failed", 240),
            }
        return summary

    @staticmethod
    def _summarize_mapping_result(result: Mapping[str, Any], *, tool_name: str = "") -> dict[str, Any]:
        keys = sorted(str(key) for key in result.keys())
        summary: dict[str, Any] = {"kind": "dict", "keys": keys}
        if "ok" in result:
            summary["ok"] = bool(result.get("ok"))
        if "success" in result:
            summary["success"] = bool(result.get("success"))
        if "error" in result and result.get("error"):
            summary["error"] = _safe_str(result.get("error"), 240)
        if "elements" in result and isinstance(result.get("elements"), list):
            summary["element_count"] = len(result.get("elements") or [])
        if "cards" in result and isinstance(result.get("cards"), list):
            summary["card_count"] = len(result.get("cards") or [])
        if "url" in result:
            summary["url"] = _sanitize_url(result.get("url"))
        if "page" in result and isinstance(result.get("page"), Mapping):
            page = result.get("page") or {}
            summary["page"] = {
                "url": _sanitize_url(page.get("url")),
                "title": _safe_str(page.get("title"), 160),
            }
        if "screenshot" in tool_name or "take_screenshot" in tool_name:
            for key in ("path", "filename", "file", "name"):
                if result.get(key):
                    summary["file"] = _safe_str(result.get(key), 180)
                    break
        if "snapshot" in tool_name:
            snapshot_text = result.get("snapshot") or result.get("text") or result.get("result")
            if isinstance(snapshot_text, str):
                summary["result_length"] = len(snapshot_text)
                summary["line_count"] = snapshot_text.count("\n") + 1 if snapshot_text else 0
        if "run_code" in tool_name or "evaluate" in tool_name:
            payload = result.get("result") or result.get("data") or result.get("text")
            if payload is not None:
                summary["result_kind"] = type(payload).__name__
                summary["result_length"] = len(str(payload))
        return summary

    @staticmethod
    def _summarize_model_response(response: Any) -> dict[str, Any]:
        content = getattr(response, "content", "")
        reasoning = getattr(response, "reasoning_content", "")
        tool_calls = getattr(response, "tool_calls", None) or []
        tool_names = []
        if isinstance(tool_calls, list):
            for call in tool_calls:
                name = getattr(call, "name", "") or _mapping_get(call, "name", "")
                if name:
                    tool_names.append(_safe_str(name, 160))
        return {
            "content_length": len(str(content or "")),
            "reasoning_length": len(str(reasoning or "")),
            "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
            "tool_names": tool_names[:12],
            "finish_reason": _safe_str(getattr(response, "finish_reason", ""), 80),
        }

    @staticmethod
    def _summarize_query(query: Any) -> dict[str, Any]:
        text = "" if query is None else str(query)
        urls = [_sanitize_url(match.group(0)) for match in _URL_RE.finditer(text)]
        domains = []
        for url in urls:
            try:
                domain = urlsplit(url).netloc
            except ValueError:
                domain = ""
            if domain and domain not in domains:
                domains.append(domain)
        
        task_hash = ""
        if text:
            task_hash = hashlib.sha256(
                text.encode("utf-8", "ignore")
            ).hexdigest()[:16]

        return {
            "length": len(text),
            "task_hash": task_hash,
            "contains_url": bool(urls),
            "url_domains": domains[:8],
            "session_id_in_prompt": BrowserSubagentStatusLogger._extract_line_value(
                text,
                "Session id",
            ),
            "request_id_in_prompt": BrowserSubagentStatusLogger._extract_line_value(
                text,
                "Request id",
            ),
            "max_steps_in_prompt": BrowserSubagentStatusLogger._extract_line_value(
                text,
                "Max steps",
            ),
            "redacted": True,
        }

    @staticmethod
    def _extract_line_value(text: str, label: str) -> str:
        prefix = f"{label}:"
        for line in text.splitlines()[:12]:
            if line.startswith(prefix):
                return _safe_str(line[len(prefix):].strip(), 120)
        return ""

    def _state(self, ctx: Any) -> dict[str, Any]:
        key = self._remember_key(ctx, self._run_key(ctx))
        return self._runs.setdefault(
            key,
            {
                "started_at": time.monotonic(),
                "model_calls": 0,
                "tool_calls": 0,
                "tool_starts": {},
                "tool_counts": Counter(),
                "total_model_elapsed_ms": 0,
                "total_tool_elapsed_ms": 0,
                "batch_calls": 0,
                "batch_steps_total": 0,
                "batch_steps_ok": 0,
                "batch_steps_failed": 0,
                "fallback_count": 0,
                "history": [],
            },
        )

    def _run_key(self, ctx: Any) -> str:
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict) and isinstance(extra.get(_STATUS_KEY), str) and extra[_STATUS_KEY]:
            return extra[_STATUS_KEY]
        session_id = self._session_id(ctx)
        if session_id:
            return session_id
        inputs = getattr(ctx, "inputs", None)
        conversation_id = _mapping_get(inputs, "conversation_id", "")
        if conversation_id:
            return str(conversation_id)
        agent = getattr(ctx, "agent", None)
        return f"agent:{id(agent)}"

    @staticmethod
    def _session_id(ctx: Any) -> str:
        session = getattr(ctx, "session", None)
        get_session_id = getattr(session, "get_session_id", None)
        if callable(get_session_id):
            try:
                session_id = get_session_id()
                if session_id:
                    return str(session_id)
            except Exception:
                return ""
        return ""

    @staticmethod
    def _remember_key(ctx: Any, key: str) -> str:
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            existing = extra.get(_STATUS_KEY)
            if isinstance(existing, str) and existing:
                return existing
            extra[_STATUS_KEY] = key
        return key

    def _metadata(self, ctx: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if self._metadata_provider is not None:
            try:
                metadata.update(dict(self._metadata_provider() or {}))
            except Exception as exc:
                metadata["metadata_error"] = _safe_str(exc, 160)
        if not metadata.get("session_id"):
            session_id = self._session_id(ctx)
            if session_id:
                metadata["session_id"] = session_id
        if not metadata.get("run_key"):
            metadata["run_key"] = self._run_key(ctx)
        return {str(key): _safe_str(value, 180) for key, value in metadata.items() if value not in (None, "")}

    def _emit(self, phase: str, ctx: Any, payload: Mapping[str, Any]) -> None:
        record: dict[str, Any] = {
            "phase": phase,
            **self._metadata(ctx),
            **dict(payload),
        }
        try:
            body = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except TypeError:
            body = json.dumps(
                {"phase": phase, "payload_error": "json_encode_failed"},
                sort_keys=True,
            )
        browser_agent_log_info("%s %s", self._marker, body)
        if isinstance(self._logger, logging.Logger):
            self._logger.info("%s %s", self._marker, body)

    @staticmethod
    def _record_tool_history(
        state: dict[str, Any],
        tool_name: str,
        elapsed_ms: Optional[int],
        result_summary: Mapping[str, Any],
    ) -> None:
        history = state.setdefault("history", [])
        entry = {
            "tool_name": _safe_str(tool_name, 120),
            "elapsed_ms": elapsed_ms,
        }
        if result_summary.get("steps_failed") is not None:
            entry["steps_failed"] = result_summary.get("steps_failed")
        if result_summary.get("ok") is not None:
            entry["ok"] = result_summary.get("ok")
        history.append(entry)
        del history[:-_TOOL_HISTORY_LIMIT]

    @staticmethod
    def _accumulate_batch_result(state: dict[str, Any], result_summary: Mapping[str, Any]) -> None:
        state["batch_calls"] = int(state.get("batch_calls", 0)) + 1
        previous_total = int(state.get("batch_steps_total", 0))
        steps_total = int(result_summary.get("steps_total") or 0)
        state["batch_steps_total"] = previous_total + steps_total
        state["batch_steps_ok"] = int(state.get("batch_steps_ok", 0)) + int(result_summary.get("steps_ok") or 0)
        failed = int(result_summary.get("steps_failed") or 0)
        state["batch_steps_failed"] = int(state.get("batch_steps_failed", 0)) + failed
        if bool(result_summary.get("had_step_errors")):
            state["last_failed_batch"] = {
                "ok": result_summary.get("ok"),
                "steps_failed": failed,
                "error": result_summary.get("error"),
                "first_failed_step": result_summary.get("first_failed_step"),
            }
        else:
            state["last_failed_batch"] = None

    @staticmethod
    def _elapsed_ms(started_at: Any) -> Optional[int]:
        if not isinstance(started_at, (int, float)):
            return None
        return int(max(0.0, (time.monotonic() - started_at) * 1000.0))

    @staticmethod
    def _tool_name_from_call(tool_call: Any) -> str:
        if isinstance(tool_call, list) and tool_call:
            return getattr(tool_call[0], "name", "") or _mapping_get(tool_call[0], "name", "")
        return getattr(tool_call, "name", "") or _mapping_get(tool_call, "name", "") or ""

    @staticmethod
    def _tool_key(inputs: Any) -> str:
        tool_call = _mapping_get(inputs, "tool_call", None)
        if isinstance(tool_call, list) and tool_call:
            tool_call = tool_call[0]
        call_id = getattr(tool_call, "id", "") or _mapping_get(tool_call, "id", "")
        if call_id:
            return str(call_id)
        return str(id(inputs))


async def install_browser_subagent_status_logging_async(
    agent: Any,
    event_enum: Any,
    *,
    logger: Optional[BrowserSubagentStatusLogger] = None,
    priority: int = 90,
) -> None:
    """Register browser subagent status callbacks on an OpenJiuwen agent."""
    status_logger = logger or BrowserSubagentStatusLogger()
    registrations = [
        (event_enum.BEFORE_INVOKE, status_logger.before_invoke),
        (event_enum.AFTER_INVOKE, status_logger.after_invoke),
        (event_enum.BEFORE_MODEL_CALL, status_logger.before_model_call),
        (event_enum.AFTER_MODEL_CALL, status_logger.after_model_call),
        (event_enum.ON_MODEL_EXCEPTION, status_logger.on_model_exception),
        (event_enum.BEFORE_TOOL_CALL, status_logger.before_tool_call),
        (event_enum.AFTER_TOOL_CALL, status_logger.after_tool_call),
        (event_enum.ON_TOOL_EXCEPTION, status_logger.on_tool_exception),
    ]
    register_callback = getattr(agent, "register_callback", None)
    if not callable(register_callback):
        raise TypeError("agent must expose register_callback(event, callback, priority=...)")
    for event, callback in registrations:
        result = register_callback(event, callback, priority=priority)
        if hasattr(result, "__await__"):
            await result
