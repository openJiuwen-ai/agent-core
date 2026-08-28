# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish selected spans as a ContextPacket (locate exam: submit_code_context)."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.query.test_paths import issue_about_tests, is_test_path
from openjiuwen.harness.schema.code_graph import CodeGraphLocation
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext
from openjiuwen.harness.tools.code_graph.finalize import emit_committed_context
from openjiuwen.harness.tools.code_graph.finish_guards import finish_guard_messages
from openjiuwen.harness.tools.code_graph.patch_context import (
    format_patch_context,
    location_from_evidence,
    merge_locations,
    normalize_submit_locations,
)

_ALLOWED_STATUS = {"COMPLETE", "PARTIAL"}


class SubmitCodeContextTool(CodeGraphBaseTool):
    """Publish selected spans and return a system-generated PATCH_CONTEXT.

    Locate-exam only. Selection guards still apply; the agent is not marked
    finished.
    """

    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__(
            "submit_code_context",
            "SubmitCodeContextTool",
            context,
            parallel_safe=False,
        )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        state = self.context.run_state
        if state is None:
            return ToolOutput(success=False, error="submit_code_context requires Code Graph run state")
        status = str(inputs.get("status") or "COMPLETE").upper()
        if status not in _ALLOWED_STATUS:
            return ToolOutput(success=False, error=f"status must be one of {sorted(_ALLOWED_STATUS)}")
        summary = str(inputs.get("summary") or "").strip() or "submitted locate context"
        added = self._ingest_locations(state, inputs.get("locations"))
        if isinstance(added, ToolOutput):
            return added
        if not state.selected:
            seeded = self._seed_from_reads(state)
            if not seeded:
                return ToolOutput(
                    success=False,
                    error=(
                        "submit_code_context blocked: pass locations from read_symbol "
                        "or call select_code_context first"
                    ),
                )
        normalized, span_blockers = normalize_submit_locations(
            state.selected,
            read_evidence=state.read_evidence,
            candidates=state.candidates,
        )
        if span_blockers:
            return ToolOutput(success=False, error=span_blockers[0])
        if not normalized:
            return ToolOutput(
                success=False,
                error=(
                    "submit_code_context blocked: no method-level spans after "
                    "shrinking large classes. inspect_code_structure then read_symbol."
                ),
            )
        state.selected = merge_locations(normalized)
        index = await self._maybe_index()
        blockers = finish_guard_messages(
            state,
            index,
            profile=state.profile,
            tool_name="submit_code_context",
        )
        if blockers:
            return ToolOutput(success=False, error=blockers[0])
        open_questions = inputs.get("open_questions")
        warnings = inputs.get("warnings")
        payload = emit_committed_context(
            state,
            status=status,
            summary=summary,
            open_questions=[str(item) for item in open_questions] if isinstance(open_questions, list) else [],
            extra_warnings=[str(item) for item in warnings] if isinstance(warnings, list) else [],
        )
        patch = format_patch_context(state.selected)
        payload["patch_context"] = patch
        payload["next_step"] = (
            "Context submitted. The system generated PATCH_CONTEXT; do not rewrite File/Lines."
        )
        return ToolOutput(success=True, data=payload)

    def _ingest_locations(self, state: Any, raw: Any) -> ToolOutput | None:
        if not isinstance(raw, list):
            return None
        query = str(getattr(getattr(state, "request", None), "query", "") or "")
        for item in raw:
            if not isinstance(item, dict):
                continue
            location = self._location_from_item(state, item)
            if location is None:
                continue
            if self.context.config.ban_tests and is_test_path(location.file) and not issue_about_tests(query):
                return ToolOutput(
                    success=True,
                    data=status_payload(
                        CodeGraphStatus.ERROR,
                        message=f"rejected test file {location.file}; only add tests if the issue is about tests",
                        extra={"file": location.file},
                    ),
                )
            if any(
                existing.file == location.file
                and int(existing.start_line) == int(location.start_line)
                and int(existing.end_line) == int(location.end_line)
                for existing in state.selected
            ):
                continue
            if len(state.selected) >= state.request.budget.max_locations:
                break
            state.selected.append(location)
        state.mark_locating()
        return None

    def _location_from_item(self, state: Any, item: dict[str, Any]) -> CodeGraphLocation | None:
        evidence_id = str(item.get("evidence_id") or "").strip()
        symbol_id = str(item.get("symbol_id") or "").strip()
        evidence = None
        if evidence_id:
            evidence = state.read_evidence.get(evidence_id)
        if evidence is None and symbol_id:
            evidence = state.candidates.get(symbol_id)
        if evidence is None and symbol_id:
            evidence = next(
                (
                    payload
                    for payload in state.read_evidence.values()
                    if str(payload.get("symbol_id") or "") == symbol_id
                ),
                None,
            )
        reason = str(item.get("reason") or item.get("role") or "selected locate span")
        if evidence is not None:
            location = location_from_evidence(evidence, reason=reason)
            if location is not None:
                if symbol_id:
                    location.symbol_id = symbol_id
                return location
        file_path = str(item.get("file") or "").replace("\\", "/").lstrip("./")
        start_raw = item.get("start_line")
        end_raw = item.get("end_line")
        if not file_path or start_raw is None or end_raw is None:
            return None
        try:
            start_line = int(start_raw)
            end_line = int(end_raw)
        except (TypeError, ValueError):
            return None
        if start_line < 1 or end_line < start_line:
            return None
        return CodeGraphLocation(
            symbol_id=symbol_id,
            file=file_path,
            start_line=start_line,
            end_line=end_line,
            reason=reason,
            confidence=0.5,
            name=str(item.get("name") or ""),
            kind=str(item.get("kind") or ""),
            evidence_id=evidence_id,
        )

    @staticmethod
    def _seed_from_reads(state: Any) -> bool:
        for payload in reversed(list(state.read_evidence.values())):
            if not isinstance(payload, dict) or not payload.get("symbol_id"):
                continue
            location = location_from_evidence(payload, reason="last read_symbol")
            if location is None:
                continue
            state.selected.append(location)
            return True
        return False

    async def _maybe_index(self):
        try:
            service = await self._service()
            return await service.ensure_ready()
        except Exception:  # noqa: BLE001 — guards degrade if the index is down
            return None
