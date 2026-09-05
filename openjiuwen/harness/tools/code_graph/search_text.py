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
        if getattr(context.run_state, "uses_focused", False):
            self._attach_match_mode_schema()

    def _attach_match_mode_schema(self) -> None:
        """Optional only on the focused card. Classic schema stays byte-stable."""
        params = dict(self.card.input_params or {})
        properties = dict(params.get("properties") or {})
        if "match_mode" in properties:
            return
        language = getattr(self.context, "language", "en")
        properties["match_mode"] = {
            "type": "string",
            "enum": ["auto", "exact", "lexical"],
            "description": (
                "auto chooses exact for quoted/error/config/decorator literals; "
                "exact is a substring scan; lexical is BM25"
                if language == "en"
                else "auto 对引号/报错/配置键/decorator 选 exact；exact 是字面量子串；lexical 是 BM25"
            ),
        }
        params["properties"] = properties
        self.card.input_params = params

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        query = str(inputs.get("query") or "").strip()
        if not query:
            return ToolOutput(success=False, error="query is required")
        state = self.context.run_state
        focused = bool(getattr(state, "uses_focused", False))
        match_mode = "lexical"
        if focused:
            from openjiuwen.harness.tools.code_graph.focused import infer_match_mode

            raw_mode = str(inputs.get("match_mode") or "auto").strip().lower()
            match_mode = infer_match_mode(query) if raw_mode in {"", "auto"} else raw_mode
            if match_mode not in {"exact", "lexical"}:
                match_mode = infer_match_mode(query)
        path_prefix = inputs.get("path_prefix")
        include_tests = bool(inputs.get("include_tests"))
        limit = self.policy.results(inputs.get("limit") or self._default_results())
        if focused and match_mode == "exact":
            output = await self._exact_search(
                query,
                path_prefix=path_prefix,
                limit=limit,
                include_tests=include_tests,
            )
        else:
            output = await self._invoke_service(
                lambda service: service.search_text(
                    query,
                    path_prefix=path_prefix,
                    limit=limit,
                    include_tests=include_tests,
                )
            )
        if not isinstance(output.data, dict):
            return output
        chunks = [item for item in (output.data.get("chunks") or output.data.get("matches") or []) if isinstance(item, dict)]
        if focused:
            from openjiuwen.harness.tools.code_graph.focused import apply_focused_observation

            apply_focused_observation(
                output.data,
                query=query,
                state=state,
                raw_items=chunks,
                matched_by=["exact" if match_mode == "exact" else "lexical"],
                empty_hint="lexical" if match_mode == "exact" else "exact",
            )
            output.data["match_mode"] = match_mode
        elif "next_actions" not in output.data and not getattr(state, "is_locate_exam", False):
            actions = text_next_actions(chunks)
            if actions:
                output.data["next_actions"] = actions
        return output

    async def _exact_search(
        self,
        query: str,
        *,
        path_prefix: str | None,
        limit: int,
        include_tests: bool,
    ) -> ToolOutput:
        from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus
        from openjiuwen.harness.tools.code_graph.focused import exact_line_hits

        budget = self._touch_budget()
        if budget is not None:
            return budget
        try:
            service = await self._service()
            index = await service._ready_for_query()
            hits = exact_line_hits(
                repo_root=self.current_repo_root(),
                index=index,
                query=query,
                path_prefix=path_prefix,
                limit=limit,
                ban_tests=bool(self.context.config.ban_tests) and not include_tests,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolOutput(success=False, error=str(exc))
        status = CodeGraphStatus.NO_MATCH if not hits else CodeGraphStatus.COMPLETE
        data = {
            "status": status.value,
            "message": (
                "search succeeded but no matches"
                if not hits
                else f"found {len(hits)} exact line hit(s)"
            ),
            "chunks": hits,
            "matches": hits,
            "match_mode": "exact",
        }
        if self.context.run_state is not None:
            self.context.run_state.remember_payload(data)
        return ToolOutput(success=True, data=data)


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
