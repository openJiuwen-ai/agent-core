# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Judge-only evidence paging and final-request size protection."""
import asyncio
import copy
import json
import logging
from pathlib import Path

from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError

TOOL_BYTES = 12000
REQUEST_BYTES = 262144
MIN_OUTPUT_TOKENS = 16384
_LOGGER = logging.getLogger(__name__)


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def bounded_text(text, offset=0):
    """Bound the serialized UTF-8 envelope, not merely the unescaped content."""
    raw = text.encode("utf-8")
    end = min(len(raw), offset + TOOL_BYTES)
    while True:
        part = raw[offset:end].decode("utf-8", errors="ignore")
        next_offset = offset + len(part.encode("utf-8"))
        result = _json({"content": part, "truncated": next_offset < len(raw),
                        "total_bytes": len(raw), "next_byte_offset": next_offset,
                        "instruction": "Use read_evidence with byte_offset or JSON pointer for more. "
                                       "Unread evidence is not absent evidence."})
        if len(result.encode("utf-8")) <= TOOL_BYTES:
            return result
        end = offset + (end - offset) * 9 // 10


def _summary(value):
    if isinstance(value, dict):
        return {"type": "object", "keys": list(value)}
    if isinstance(value, list):
        return {"type": "array", "count": len(value)}
    return value


class JudgeEvidenceTool(Tool):
    """Read only within the frozen judge workspace, including JSON Pointer pages."""

    def __init__(self, workspace, agent_id):
        super().__init__(ToolCard(
            id=f"{agent_id}.read_evidence", name="read_evidence",
            description="Read frozen evidence by byte page or JSON Pointer. JSON objects return child metadata; "
                        "arrays return a page. Use this for large single-line JSON instead of grep.",
            input_params={"type": "object", "properties": {
                "path": {"type": "string"}, "pointer": {"type": "string"},
                "byte_offset": {"type": "integer", "minimum": 0},
                "item_offset": {"type": "integer", "minimum": 0},
            }, "required": ["path"]}, idempotent=True,
        ))
        self.workspace = Path(workspace).resolve()

    def _read(self, inputs):
        path = (self.workspace / inputs["path"]).resolve()
        if not path.is_relative_to(self.workspace) or not path.is_file():
            raise ValueError("Evidence path must be a file inside the frozen workspace")
        offset, item_offset = inputs.get("byte_offset", 0), inputs.get("item_offset", 0)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (offset, item_offset)):
            raise ValueError("Offsets must be non-negative integers")
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Evidence exceeds the 64 MiB structured-read limit")
        text = path.read_text(encoding="utf-8-sig")
        if "pointer" in inputs:
            value, pointer = json.loads(text), inputs["pointer"]
            if pointer and not pointer.startswith("/"):
                raise ValueError("JSON pointer must be empty or begin with /")
            for token in pointer[1:].split("/") if pointer else []:
                key = token.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list) and not key.isdigit():
                    raise ValueError("Array pointer must use a non-negative index")
                value = value[int(key)] if isinstance(value, list) else value[key]
            if isinstance(value, dict):
                value = {k: _summary(v) for k, v in value.items()}
            elif isinstance(value, list):
                page = value[item_offset:item_offset + 10]
                value = {"count": len(value), "offset": item_offset,
                         "items": page}
            text = _json(value)
        return ToolOutput(success=True, data={"content": bounded_text(text, offset)})

    async def invoke(self, inputs, **kwargs):
        try:
            return await asyncio.to_thread(self._read, inputs)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            return ToolOutput(success=False, error=bounded_text(str(exc)))

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def _request_bound(rows, schemas):
    # UTF-8 bytes deliberately overestimate text tokens; include serialization and framing.
    return len(_json(rows).encode("utf-8")) + len(_json(schemas).encode("utf-8")) + 4096


def guard_messages(messages, tools=None, *, limit=REQUEST_BYTES, reserve=16384):
    """Use UTF-8 bytes as a conservative token bound, including tool schemas."""
    rows = [{"role": "user", "content": messages}] if isinstance(messages, str) else [
        m.model_dump(mode="json", exclude_none=True) if hasattr(m, "model_dump") else copy.deepcopy(m)
        for m in messages
    ]
    schemas = [t.model_dump(mode="json") if hasattr(t, "model_dump") else t for t in (tools or [])]

    def size():
        return _request_bound(rows, schemas) + reserve

    for row in rows:
        if row.get("role") == "tool" and len(_json(row.get("content")).encode("utf-8")) > TOOL_BYTES:
            row["content"] = bounded_text(str(row.get("content", "")))
    for row in rows:
        if size() <= limit:
            break
        if row.get("role") == "tool":
            row["content"] = ("Evidence evicted for context budget. Re-read the original tool path with "
                              "read_evidence and a precise JSON pointer or byte_offset. Do not infer absence.")
    if size() > limit:
        raise EvaluationInfrastructureError(
            "Judge request exceeds context budget after evidence reduction: "
            f"input_token_upper_bound={size() - reserve}, output_reserve={reserve}, "
            f"context_budget={limit}; complete grading evidence was not truncated"
        )
    if not isinstance(messages, str):
        return [original.model_copy(update={"content": row.get("content")})
                if hasattr(original, "model_copy") else row for original, row in zip(messages, rows)]
    return rows


class GuardedJudgeModel(Model):
    """Guard the actual model boundary, not the pre-rail message preview."""

    def _prepare(self, messages, kwargs):
        """Reduce output headroom only after trying to reclaim tool history."""
        options = dict(kwargs)
        requested = int(options.get("max_tokens") or self.model_config.max_tokens or MIN_OUTPUT_TOKENS)
        window = ContextUtils.resolve_context_max(
            model_name=self.model_config.model_name,
            fallback_context_window_tokens=self.model_config.context_window,
        )
        if requested <= 0 or window <= 0:
            raise EvaluationInfrastructureError("Judge context window and output limit must be positive")
        tools = options.get("tools")
        try:
            guarded = guard_messages(messages, tools, limit=window, reserve=requested)
        except EvaluationInfrastructureError:
            floor = min(requested, MIN_OUTPUT_TOKENS)
            guarded = guard_messages(messages, tools, limit=window, reserve=floor)
            rows = [m.model_dump(mode="json", exclude_none=True) if hasattr(m, "model_dump") else m
                    for m in guarded]
            schemas = [t.model_dump(mode="json") if hasattr(t, "model_dump") else t for t in (tools or [])]
            available = window - _request_bound(rows, schemas)
            options["max_tokens"] = min(requested, available)
            _LOGGER.warning(
                "Judge output budget adjusted: requested=%s effective=%s context_budget=%s; "
                "grading evidence preserved", requested, options["max_tokens"], window,
            )
        return guarded, options

    async def invoke(self, messages, **kwargs):
        messages, kwargs = self._prepare(messages, kwargs)
        return await super().invoke(messages, **kwargs)

    async def stream(self, messages, **kwargs):
        messages, kwargs = self._prepare(messages, kwargs)
        async for chunk in super().stream(messages, **kwargs):
            yield chunk
