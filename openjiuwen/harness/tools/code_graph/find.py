# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""find_* tools: exact resolve, symbol reads, typed relation hops.

This is the model-visible Code Graph surface. Implementation helpers live in
sibling modules (search_code, read_code, …) and register under these public
names.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext
from openjiuwen.harness.tools.code_graph.commit_code_context import SubmitCodeContextTool
from openjiuwen.harness.tools.code_graph.search_code import FindCodeSymbolsTool
from openjiuwen.harness.tools.code_graph.search_text import SearchSourceTextTool
from openjiuwen.harness.tools.code_graph.select_context import SelectCodeContextTool
from openjiuwen.harness.tools.code_graph.trace_call_chain import TraceCallPathsTool

_RELATION_DEFAULT = 10
_WHOLE_FILE_KINDS = {"file", "module"}
_WHOLE_FILE_LINES = 80

__all__ = [
    "FindBaseClassesTool",
    "FindCalleesTool",
    "FindCallersTool",
    "FindCodeSymbolsTool",
    "FindImportersTool",
    "FindSubclassesTool",
    "InspectCodeStructureTool",
    "ReadSymbolTool",
    "ResolveSymbolTool",
    "SearchSourceTextTool",
    "SelectCodeContextTool",
    "SubmitCodeContextTool",
    "TraceCallPathsTool",
]


class ResolveSymbolTool(CodeGraphBaseTool):
    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__("resolve_symbol", "ResolveSymbolTool", context)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        name = str(inputs.get("name") or inputs.get("symbol_id") or "").strip()
        if not name:
            return ToolOutput(success=False, error="name is required")
        return await self._invoke_service(
            lambda service: service.resolve_symbol(
                name,
                kind=str(inputs.get("kind") or "").strip() or None,
                path_hint=str(inputs.get("path_hint") or "").strip() or None,
                limit=self.policy.results(inputs.get("limit") or 8),
            )
        )


class InspectCodeStructureTool(CodeGraphBaseTool):
    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__("inspect_code_structure", "InspectCodeStructureTool", context)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        file_path = str(inputs.get("file") or "").strip() or None
        parent = str(inputs.get("parent_symbol") or "").strip() or None
        if not file_path and not parent:
            return ToolOutput(success=False, error="file or parent_symbol is required")
        kinds = inputs.get("kinds")
        if kinds is not None and not isinstance(kinds, list):
            kinds = [kinds]
        return await self._invoke_service(
            lambda service: service.list_symbols(
                file=file_path,
                parent_symbol=parent,
                kinds=kinds,
                depth=self.policy.depth(inputs.get("depth") or 1),
                limit=self.policy.nodes(inputs.get("limit")),
            )
        )


class ReadSymbolTool(CodeGraphBaseTool):
    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__("read_symbol", "ReadSymbolTool", context)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        symbol_id = str(inputs.get("symbol_id") or "").strip()
        if not symbol_id:
            return ToolOutput(success=False, error="symbol_id is required")
        from openjiuwen.core.retrieval.code_graph.service import MAX_SYMBOL_CONTEXT

        before = inputs.get("context_before")
        after = inputs.get("context_after")
        before_n = int(before if before is not None else 5)
        after_n = int(after if after is not None else 5)
        output = await self._invoke_service(
            lambda service: service.read_symbol(
                symbol_id,
                context_before=min(MAX_SYMBOL_CONTEXT, max(0, before_n)),
                context_after=min(MAX_SYMBOL_CONTEXT, max(0, after_n)),
            )
        )
        return output


class _FindRelationTool(CodeGraphBaseTool):
    tool_name = ""
    class_id = ""
    relation = ""

    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__(self.tool_name, self.class_id, context)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        symbol_id = str(inputs.get("symbol_id") or "").strip()
        if not symbol_id:
            return ToolOutput(success=False, error="symbol_id is required")
        return await self._invoke_service(
            lambda service: service.expand_related(
                symbol_id,
                relations=[self.relation],
                depth=1,
                limit=self.policy.results(inputs.get("limit") or _RELATION_DEFAULT),
            )
        )


class FindCallersTool(_FindRelationTool):
    tool_name = "find_callers"
    class_id = "FindCallersTool"
    relation = "called_by"

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        output = await super().invoke(inputs, **kwargs)
        if not isinstance(output.data, dict):
            return output
        actions = caller_next_actions(
            output.data.get("related") or [],
            output.data.get("unresolved") or [],
        )
        if actions:
            output.data["next_actions"] = actions
        return output


class FindCalleesTool(_FindRelationTool):
    tool_name = "find_callees"
    class_id = "FindCalleesTool"
    relation = "calls"

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        output = await super().invoke(inputs, **kwargs)
        if not isinstance(output.data, dict):
            return output
        actions = callee_next_actions(output.data.get("unresolved") or [])
        if actions:
            output.data["next_actions"] = actions
        return output


class FindImportersTool(_FindRelationTool):
    tool_name = "find_importers"
    class_id = "FindImportersTool"
    relation = "imported_by"

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        output = await super().invoke(inputs, **kwargs)
        if not isinstance(output.data, dict):
            return output
        related = [item for item in (output.data.get("related") or []) if isinstance(item, dict)]
        related = _prefer_production_importers(related)
        output.data["related"] = related
        output.data["related_count"] = len(related)
        actions = importer_next_actions(related)
        if actions:
            output.data["next_actions"] = actions
        return output


def _prefer_production_importers(related: list[dict[str, Any]]) -> list[dict[str, Any]]:
    production = [item for item in related if not is_test_path(str(item.get("file") or ""))]
    tests = [item for item in related if is_test_path(str(item.get("file") or ""))]
    return production + tests


def importer_next_actions(related: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Follow production importers; do not recommend reading a whole test file."""
    actions: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    production = [item for item in related if not is_test_path(str(item.get("file") or ""))]
    pool = production or related
    for item in pool[:5]:
        file_path = str(item.get("file") or "")
        symbol_id = str(item.get("symbol_id") or "")
        kind = str(item.get("kind") or "").lower()
        span = int(item.get("end_line") or 0) - int(item.get("start_line") or 0)
        name = str(item.get("name") or symbol_id)
        if not file_path or file_path in seen_files:
            continue
        seen_files.add(file_path)
        if kind in _WHOLE_FILE_KINDS or span > _WHOLE_FILE_LINES:
            actions.append(
                {
                    "tool": "inspect_code_structure",
                    "file": file_path,
                    "reason": f"list members of importer {name}",
                }
            )
        elif symbol_id:
            actions.append(
                {
                    "tool": "read_symbol",
                    "symbol_id": symbol_id,
                    "reason": f"read importer {name}",
                }
            )
        else:
            actions.append(
                {
                    "tool": "search_source_text",
                    "query": name.rsplit("::", 1)[-1],
                    "path_prefix": "/".join(file_path.split("/")[:-1]) or file_path,
                    "reason": f"find registration / transform text in {file_path}",
                }
            )
    return actions


def caller_next_actions(
    related: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Duck-typed unresolved callers first; same-file self-calls fill the rest.

    ``QuerySet.filter`` has many SAME_CLASS callers in ``query.py``. If those
    occupy the five next_actions slots, ``queryset.filter(...)`` sites such as
    ``get_search_results`` never get a hop even though they are in unresolved.
    """
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(symbol_id: str, file_path: str, reason: str) -> None:
        if not symbol_id or symbol_id in seen or len(actions) >= 5:
            return
        seen.add(symbol_id)
        action: dict[str, Any] = {
            "tool": "read_symbol",
            "symbol_id": symbol_id,
            "reason": reason,
        }
        if file_path:
            action["file"] = file_path
        actions.append(action)

    production_related = [item for item in related if not is_test_path(str(item.get("file") or ""))]
    production_unresolved = [
        item for item in unresolved if not is_test_path(str(item.get("file") or ""))
    ]
    related_pool = production_related or related
    unresolved_pool = production_unresolved or unresolved
    for item in unresolved_pool:
        _add(
            str(item.get("caller_id") or ""),
            str(item.get("file") or ""),
            f"read unresolved caller of {item.get('callee_name') or 'this method'}",
        )
    for item in related_pool:
        _add(
            str(item.get("symbol_id") or ""),
            str(item.get("file") or ""),
            f"read caller {item.get('name') or item.get('symbol_id')}",
        )
    return actions[:5]


def callee_next_actions(unresolved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Point at names the current symbol calls that did not become edges."""
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    production = [item for item in unresolved if not is_test_path(str(item.get("file") or ""))]
    pool = production or unresolved
    for item in pool:
        name = str(item.get("callee_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        actions.append(
            {
                "tool": "resolve_symbol",
                "name": name,
                "reason": f"unresolved callee {name}; resolve instead of guessing",
            }
        )
        if len(actions) >= 5:
            break
    return actions


class FindBaseClassesTool(_FindRelationTool):
    tool_name = "find_base_classes"
    class_id = "FindBaseClassesTool"
    relation = "inherits"


class FindSubclassesTool(_FindRelationTool):
    tool_name = "find_subclasses"
    class_id = "FindSubclassesTool"
    relation = "inherited_by"

