# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Programmatic previews of large tool bodies; source nodes remain untouched."""

import json
import re

TOOL_TEXT_PREVIEW_CHARS = 1000
_BODY_FIELDS = {"content", "text", "body", "code", "script", "html", "old_string", "new_string",
                "patch", "diff", "stdout", "stderr", "output", "result", "data", "message",
                "prompt", "system_prompt"}
_ERROR = re.compile(r"error|exception|traceback|\bfail(?:ed|ure)?\b|assert|not found|错误|失败", re.IGNORECASE)
_RESULT = re.compile(r"exit.?code|\bpassed?\b|summary|通过|结论", re.IGNORECASE)
_SOURCE_CUT = re.compile(r"<OTel attribute truncated:|<truncated \d+ chars>")


def tool_view(data: dict) -> dict:
    """Project already-copied node data; keep identities, paths, commands and status fields."""
    previews, source_cuts = {}, []

    def text(value: str, path: str) -> str:
        if _SOURCE_CUT.search(value):
            source_cuts.append(path)
        if len(value) <= TOOL_TEXT_PREVIEW_CHARS:
            return value
        previews[path] = len(value)
        # Retain some middle error/result lines as well as the beginning and end.
        head, tail = value[:350], value[-250:]
        errors, results = [], []
        for line in value.splitlines():
            line = line.strip()
            if not line or line in head or line in tail:
                continue
            if _ERROR.search(line) and len(errors) < 2:
                errors.append(line[:120])
            elif _RESULT.search(line) and len(results) < 2:
                results.append(line[:120])
        selected = "\n".join(errors + results)[:250]
        marker = f"\n[tool body preview: {len(value)} chars; middle omitted; read original node for details]\n"
        return head + marker + selected + "\n[...]\n" + tail

    def body(value: object, path: str) -> object:
        if isinstance(value, dict):
            return {key: text(item, f"{path}.{key}") if key in _BODY_FIELDS and isinstance(item, str)
                    else body(item, f"{path}.{key}") for key, item in value.items()}
        if isinstance(value, list):
            return [body(item, f"{path}[{i}]") for i, item in enumerate(value)]
        return value

    def serialized(value: object, path: str) -> object:
        if not isinstance(value, str):
            return body(value, path)
        if _SOURCE_CUT.search(value):
            source_cuts.append(path)
        try:
            parsed = json.loads(value)
        except ValueError:
            # OTel may already have cut the middle of a JSON argument.
            return text(value, path)
        if not isinstance(parsed, (dict, list)):
            return text(value, path)
        projected = body(parsed, path)
        return value if projected == parsed else json.dumps(projected, ensure_ascii=False)

    for index, call in enumerate(data.get("tool_calls") or []):
        function = call.get("function") or call
        if not isinstance(function, dict):
            continue
        if "arguments" in function:
            function["arguments"] = serialized(function["arguments"], f"tool_calls[{index}].arguments")
    if data.get("role") == "tool" and isinstance(data.get("content"), str):
        data["content"] = serialized(data["content"], "content")
    if previews:
        data["previewed_fields"] = previews
    if source_cuts:
        data["source_truncated_fields"] = list(dict.fromkeys(source_cuts))
    return data
