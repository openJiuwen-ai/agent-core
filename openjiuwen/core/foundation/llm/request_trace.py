# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Opt-in LLM request tracing for local trajectory analysis."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

TRACE_DIR_ENV = "OPENJIUWEN_LLM_TRACE_DIR"
_REDACTED = "***REDACTED***"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "proxy-authorization",
    "x-api-key",
}


class LLMRequestTrace:
    """A single request trace persisted as one JSON document."""

    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = path
        self.payload = payload
        self._write()

    @classmethod
    def start(
            cls,
            *,
            provider: Any,
            api_base: str,
            api_key: str,
            stream: bool,
            request: dict[str, Any],
    ) -> "LLMRequestTrace | None":
        trace_dir = os.getenv(TRACE_DIR_ENV)
        if not trace_dir:
            return None

        path = Path(trace_dir)
        path.mkdir(parents=True, exist_ok=True)
        request_id = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc)
        timestamp_text = timestamp.isoformat()
        filename_timestamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        payload = {
            "trace_id": request_id,
            "timestamp": timestamp_text,
            "provider": getattr(provider, "value", str(provider)),
            "api_base": api_base,
            "api_key": _REDACTED if api_key else "",
            "stream": stream,
            "request": _to_jsonable(request),
        }
        return cls(path / f"{filename_timestamp}_{request_id}.json", payload)

    def record_response(self, response: Any) -> None:
        self.payload["response"] = _to_jsonable(response)
        self.payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._write()

    def record_error(self, exc: BaseException) -> None:
        self.payload["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        self.payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._write()

    def _write(self) -> None:
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(self.payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _to_jsonable(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return {
            str(key): _REDACTED if _is_sensitive_key(key) else _to_jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump(exclude_none=True))
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _to_jsonable(vars(value))
        except Exception:
            pass
    return str(value)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("_", "-")
    return normalized in _SENSITIVE_KEYS
