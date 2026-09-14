# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext


class SearchSourceTextTool(CodeGraphBaseTool):
    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__("search_source_text", "SearchSourceTextTool", context)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        query = str(inputs.get("query") or "").strip()
        if not query:
            return ToolOutput(success=False, error="query is required")
        output = await self._invoke_service(
            lambda service: service.search_text(
                query,
                path_prefix=inputs.get("path_prefix"),
                limit=self.policy.results(inputs.get("limit") or self._default_results()),
                include_tests=bool(inputs.get("include_tests")),
            )
        )
        if isinstance(output.data, dict) and "next_actions" not in output.data:
            state = self.context.run_state
            if not getattr(state, "is_locate_exam", False):
                actions = text_next_actions(output.data.get("chunks") or [])
                if actions:
                    output.data["next_actions"] = actions
        return output


def text_next_actions(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Point at the symbol that owns a text hit, instead of another reworded search."""
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    production = [item for item in chunks if not is_test_path(str(item.get("file") or ""))]
    pool = production or list(chunks)
    for item in pool:
        if not isinstance(item, dict):
            continue
        symbol_id = str(item.get("symbol_id") or "").strip()
        file_path = str(item.get("file") or "").strip()
        name = str(item.get("name") or symbol_id or file_path)
        if symbol_id:
            if symbol_id in seen:
                continue
            seen.add(symbol_id)
            actions.append(
                {
                    "tool": "read_symbol",
                    "symbol_id": symbol_id,
                    "file": file_path or None,
                    "reason": f"read the definition that matched {name}",
                }
            )
        elif file_path and file_path not in seen:
            seen.add(file_path)
            actions.append(
                {
                    "tool": "inspect_code_structure",
                    "file": file_path,
                    "reason": f"list symbols around the text hit in {file_path}",
                }
            )
        if len(actions) >= 3:
            break
    return actions
