# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded native search over the frozen judge evidence, without shell execution."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
from pathlib import Path

from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.rsi.harness_rsi.evaluator.judger.evidence_guard import TOOL_BYTES


class JudgeSearchTool(Tool):
    """Literal search avoids external executables and unbounded regex evaluation."""

    MAX_FILE_BYTES = 1024 * 1024
    MAX_TOTAL_BYTES = 16 * 1024 * 1024
    MAX_ENTRIES = 4096

    def __init__(self, workspace: Path, agent_id: str) -> None:
        super().__init__(ToolCard(
            id=f"{agent_id}.grep", name="grep",
            description="Search a literal substring in frozen UTF-8 evidence without executing commands. "
                        "Returns bounded file/line matches. An incomplete scan or no match is not proof of absence. "
                        "Use read_evidence for full text and large files. Regex syntax is treated literally.",
            input_params={"type": "object", "properties": {
                "pattern": {"type": "string", "minLength": 1, "maxLength": 512},
                "path": {"type": "string", "description": "File or directory inside evidence; default '.'"},
                "glob": {"type": "string"}, "ignore_case": {"type": "boolean"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
            }, "required": ["pattern"], "additionalProperties": False}, idempotent=True,
        ))
        self.workspace = Path(workspace).resolve()

    def _search(self, inputs: dict) -> ToolOutput:
        pattern = inputs.get("pattern")
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= 512:
            raise ValueError("pattern must be a literal string of 1 to 512 characters")
        limit = inputs.get("max_results", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("max_results must be between 1 and 100")
        start = (self.workspace / inputs.get("path", ".")).resolve()
        if not start.is_relative_to(self.workspace) or not start.exists():
            raise ValueError("Search path must exist inside the frozen workspace")
        ignore_case = inputs.get("ignore_case", False)
        needle = pattern.casefold() if ignore_case else pattern
        matches, limitations, pending = [], set(), [start]
        entries, total = 0, 0
        while pending:
            path = pending.pop()
            entries += 1
            if entries > self.MAX_ENTRIES:
                limitations.add("entry_limit")
                break
            if path.is_symlink() or not path.resolve().is_relative_to(self.workspace):
                limitations.add("symlink_skipped")
                continue
            try:
                if path.is_dir():
                    # Do not follow directory links; bound discovery as well as file reads.
                    with os.scandir(path) as children:
                        for child in children:
                            if entries + len(pending) >= self.MAX_ENTRIES:
                                limitations.add("entry_limit")
                                break
                            pending.append(Path(child.path))
                    continue
                if not path.is_file():
                    limitations.add("non_regular_file")
                    continue
                relative = path.relative_to(self.workspace).as_posix()
                if not fnmatch.fnmatch(relative, inputs.get("glob", "*")):
                    continue
                remaining = self.MAX_TOTAL_BYTES - total
                if remaining <= 0:
                    limitations.add("total_byte_limit")
                    break
                size_limit = min(self.MAX_FILE_BYTES, remaining)
                with path.open("rb") as stream:
                    raw = stream.read(size_limit + 1)
                total += len(raw)
                if len(raw) > size_limit:
                    limitations.add("file_byte_limit")
                    raw = raw[:size_limit]
                if b"\0" in raw:
                    limitations.add("binary_file_skipped")
                    continue
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    limitations.add("non_utf8_or_partial_character")
                    text = raw.decode("utf-8-sig", errors="replace")
                for number, line in enumerate(text.splitlines(), 1):
                    haystack = line.casefold() if ignore_case else line
                    if needle not in haystack:
                        continue
                    index = haystack.find(needle)
                    excerpt = line[max(0, index - 80):index + 240]
                    match = {"path": relative, "line": number, "text": excerpt,
                             "line_truncated": len(excerpt) != len(line)}
                    # Keep the actual result valid JSON within the tool byte envelope.
                    if len(json.dumps(matches + [match], ensure_ascii=False).encode("utf-8")) > TOOL_BYTES - 1500:
                        limitations.add("output_byte_limit")
                        break
                    matches.append(match)
                    if len(matches) >= limit:
                        limitations.add("result_limit")
                        break
                if {"output_byte_limit", "result_limit"}.intersection(limitations):
                    break
            except OSError:
                limitations.add("unreadable_path")
        payload = {"matches": matches, "scan_complete": not limitations,
                   "limitations": sorted(limitations),
                   "instruction": "Search misses and omitted evidence are not proof of absence. "
                                  "Read relevant paths with read_evidence for complete content."}
        return ToolOutput(success=True, data={"content": json.dumps(payload, ensure_ascii=False)})

    async def invoke(self, inputs, **kwargs):
        try:
            return await asyncio.to_thread(self._search, inputs)
        except (OSError, ValueError, TypeError) as exc:
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)
