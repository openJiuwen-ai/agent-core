# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ACI focus tool: turn one candidate into the current edit window."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext
from openjiuwen.harness.tools.code_graph.focused import (
    format_focus_display,
    focus_line_window,
)
from openjiuwen.harness.tools.code_graph.select_context import SelectCodeContextTool


class FocusCodeTool(CodeGraphBaseTool):
    """Model-facing name for selecting a candidate, then showing 50–100 lines."""

    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__("focus_code", "FocusCodeTool", context, parallel_safe=False)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        state = self.context.run_state
        if state is None:
            return ToolOutput(success=False, error="focus_code requires Code Graph run state")
        reason = str(inputs.get("reason") or "").strip()
        if not reason:
            return ToolOutput(success=False, error="reason is required")
        evidence = self._lookup_candidate(state, inputs)
        if evidence is None:
            return ToolOutput(
                success=True,
                data=status_payload(
                    CodeGraphStatus.ERROR,
                    message="candidate_id / symbol_id was not returned by a previous search",
                    extra={
                        "candidate_id": str(inputs.get("candidate_id") or ""),
                        "symbol_id": str(inputs.get("symbol_id") or ""),
                    },
                ),
            )
        symbol_id = str(evidence.get("symbol_id") or "").strip()
        if symbol_id:
            state.candidates.setdefault(symbol_id, dict(evidence))
        select_inputs = {
            "symbol_id": symbol_id,
            "file": str(evidence.get("file") or ""),
            "start_line": evidence.get("start_line"),
            "end_line": evidence.get("end_line"),
            "name": str(evidence.get("name") or ""),
            "kind": str(evidence.get("kind") or ""),
            "reason": reason,
            "confidence": inputs.get("confidence"),
            "evidence_id": str(evidence.get("evidence_id") or ""),
        }
        selected = await SelectCodeContextTool(self.context).invoke(select_inputs)
        if not selected.success:
            return selected
        payload = dict(selected.data or {})
        if str(payload.get("status") or "") in {CodeGraphStatus.ERROR.value, CodeGraphStatus.PARTIAL.value}:
            return selected
        file_path = str(payload.get("file") or evidence.get("file") or "")
        start = int(payload.get("start_line") or evidence.get("start_line") or 1)
        end = int(payload.get("end_line") or evidence.get("end_line") or start)
        matched = evidence.get("start_line")
        try:
            matched_line = int(matched) if matched not in (None, "") else start
        except (TypeError, ValueError):
            matched_line = start
        file_len = _file_line_count(self.current_repo_root(), file_path)
        window_start, window_end = focus_line_window(
            start,
            end,
            matched_line=matched_line,
            file_len=file_len,
        )
        source = ""
        try:
            service = await self._service()
            read = await service.read_code(file_path, start_line=window_start, end_line=window_end)
        except Exception as exc:  # noqa: BLE001 — still keep the selected target
            read = {"message": str(exc)}
        if isinstance(read, dict):
            source = str(read.get("content") or read.get("source") or read.get("text") or "")
            if not source and isinstance(read.get("lines"), list):
                source = "\n".join(str(line) for line in read["lines"])
        supporting = await self._supporting_evidence(str(payload.get("symbol_id") or evidence.get("symbol_id") or ""))
        candidate_id = str(evidence.get("candidate_id") or inputs.get("candidate_id") or "")
        role = str(evidence.get("role") or "implementation")
        symbol = str(payload.get("symbol_id") or evidence.get("symbol_id") or payload.get("name") or "")
        focus = {
            "candidate_id": candidate_id,
            "symbol_id": symbol,
            "file": file_path,
            "start_line": window_start,
            "end_line": window_end,
            "role": role,
            "name": str(payload.get("name") or evidence.get("name") or ""),
        }
        state.current_focus = focus
        display = format_focus_display(
            candidate_id=candidate_id,
            file_path=file_path,
            symbol=symbol or str(focus.get("name") or ""),
            start_line=window_start,
            end_line=window_end,
            role=role,
            source=source,
            supporting=supporting,
        )
        return ToolOutput(
            success=True,
            data={
                "status": "COMPLETE",
                "focused": True,
                "phase": state.phase,
                "selected_count": len(state.selected),
                "candidate_id": candidate_id,
                "symbol_id": symbol,
                "file": file_path,
                "start_line": window_start,
                "end_line": window_end,
                "role": role,
                "name": focus["name"],
                "source": source,
                "supporting_evidence": supporting,
                "display": display,
                "next_actions": [
                    {
                        "action": "edit",
                        "reason": (
                            "Edit this implementation, or call focus_code on another "
                            "candidate if the evidence proves this target is wrong."
                        ),
                    }
                ],
            },
        )

    @staticmethod
    def _lookup_candidate(state: Any, inputs: dict[str, Any]) -> dict[str, Any] | None:
        candidate_id = str(inputs.get("candidate_id") or "").strip()
        if candidate_id:
            hit = (getattr(state, "focused_candidates", None) or {}).get(candidate_id)
            if isinstance(hit, dict):
                return hit
        symbol_id = str(inputs.get("symbol_id") or "").strip()
        if symbol_id:
            focused = getattr(state, "focused_candidates", None) or {}
            for item in focused.values():
                if isinstance(item, dict) and str(item.get("symbol_id") or "") == symbol_id:
                    return item
            candidates = getattr(state, "candidates", None) or {}
            hit = candidates.get(symbol_id)
            if isinstance(hit, dict):
                payload = dict(hit)
                payload.setdefault("symbol_id", symbol_id)
                return payload
        return None

    async def _supporting_evidence(self, symbol_id: str) -> list[str]:
        if not symbol_id:
            return []
        try:
            service = await self._service()
            payload = await service.expand_related(
                symbol_id,
                relations=("inherits", "calls", "called_by"),
                depth=1,
                limit=4,
            )
        except Exception:  # noqa: BLE001 — focus still returns the window
            return []
        if not isinstance(payload, dict):
            return []
        notes: list[str] = []
        for item in payload.get("related") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("symbol_id") or "")
            relation = str(item.get("relation") or item.get("kind") or "related")
            file_path = str(item.get("file") or "")
            if name:
                notes.append(f"{name} ({relation}{f', {file_path}' if file_path else ''})")
            if len(notes) >= 3:
                break
        return notes


def _file_line_count(repo_root: str, file_path: str) -> int | None:
    if not file_path:
        return None
    path = Path(repo_root) / file_path.replace("\\", "/")
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return None
