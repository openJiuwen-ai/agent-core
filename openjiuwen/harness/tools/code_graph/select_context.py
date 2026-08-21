# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.query.test_paths import issue_about_tests, is_test_path
from openjiuwen.harness.schema.code_graph import CodeGraphLocation
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext


class SelectCodeContextTool(CodeGraphBaseTool):
    def __init__(self, context: CodeGraphToolContext) -> None:
        super().__init__(
            "select_code_context",
            "SelectCodeContextTool",
            context,
            parallel_safe=False,
        )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        state = self.context.run_state
        if state is None:
            return ToolOutput(
                success=False,
                error=f"{self.card.name} requires Code Graph run state",
            )
        budget = self._touch_budget()
        if budget is not None:
            return budget
        reason = str(inputs.get("reason") or "").strip()
        if not reason:
            return ToolOutput(success=False, error="reason is required")
        from openjiuwen.core.retrieval.code_graph.query.resolve_symbol import strip_file_uri

        raw_symbol_id = str(inputs.get("symbol_id") or "").strip()
        symbol_id = strip_file_uri(raw_symbol_id)
        file_path = str(inputs.get("file") or "").replace("\\", "/").lstrip("./")
        start_raw = inputs.get("start_line")
        end_raw = inputs.get("end_line")
        evidence: dict[str, Any] | None = None
        if symbol_id:
            evidence = _candidate_for(state, symbol_id)
            if evidence is None:
                evidence = await self._resolve_symbol_evidence(symbol_id)
            if evidence is None:
                return ToolOutput(
                    success=True,
                    data=status_payload(
                        CodeGraphStatus.ERROR,
                        message="symbol_id was not returned by a previous Code Graph tool",
                        extra={"symbol_id": raw_symbol_id or symbol_id},
                    ),
                )
            file_path = file_path or str(evidence.get("file") or "")
            start_line = int(start_raw if start_raw is not None else evidence.get("start_line") or 0)
            end_line = int(end_raw if end_raw is not None else evidence.get("end_line") or 0)
            name = str(evidence.get("name") or "")
            kind = str(evidence.get("kind") or "")
        else:
            if not file_path or start_raw is None or end_raw is None:
                return ToolOutput(
                    success=False,
                    error="provide symbol_id or file + start_line + end_line",
                )
            try:
                start_line = int(start_raw)
                end_line = int(end_raw)
            except (TypeError, ValueError):
                return ToolOutput(success=False, error="start_line and end_line must be integers")
            if start_line < 1 or end_line < start_line:
                return ToolOutput(success=False, error="invalid line range")
            if not _has_file_evidence(state, file_path):
                return ToolOutput(
                    success=True,
                    data=status_payload(
                        CodeGraphStatus.ERROR,
                        message=(
                            "file was not returned by a previous Code Graph tool "
                            "(find_code_symbols, search_source_text, read_symbol, "
                            "read_code, or inspect_code_structure)"
                        ),
                        extra={"file": file_path},
                    ),
                )
            try:
                self._service_sync_resolve(file_path)
            except Exception as exc:  # noqa: BLE001
                return ToolOutput(
                    success=True,
                    data=status_payload(CodeGraphStatus.ERROR, message=str(exc), extra={"file": file_path}),
                )
            name = str(inputs.get("name") or "")
            kind = str(inputs.get("kind") or "")
        file_path = file_path.replace("\\", "/").lstrip("./")
        if self.context.config.ban_tests and is_test_path(file_path) and not issue_about_tests(state.request.query):
            return ToolOutput(
                success=True,
                data=status_payload(
                    CodeGraphStatus.ERROR,
                    message=(
                        f"{self.card.name} rejected: '{file_path}' looks like a test file. "
                        "Only add production code unless the issue is specifically about tests."
                    ),
                    extra={"symbol_id": symbol_id, "file": file_path},
                ),
            )
        if any(
            item.file.replace("\\", "/") == file_path
            and int(item.start_line) == int(start_line)
            and int(item.end_line) == int(end_line)
            for item in state.selected
        ):
            return ToolOutput(
                success=True,
                data={
                    "status": "COMPLETE",
                    "selected_count": len(state.selected),
                    "symbol_id": symbol_id,
                    "file": file_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "duplicate": True,
                },
            )
        if len(state.selected) >= state.request.budget.max_locations:
            return ToolOutput(
                success=True,
                data=status_payload(
                    CodeGraphStatus.PARTIAL,
                    message="max_locations budget reached",
                ),
            )
        confidence = inputs.get("confidence")
        try:
            conf = float(confidence) if confidence is not None else 0.0
        except (TypeError, ValueError):
            conf = 0.0
        location = CodeGraphLocation(
            symbol_id=symbol_id,
            file=file_path,
            start_line=int(start_line),
            end_line=int(end_line),
            reason=reason,
            confidence=max(0.0, min(1.0, conf)),
            name=name,
            kind=kind,
            evidence_id=str(inputs.get("evidence_id") or (evidence or {}).get("evidence_id") or ""),
        )
        state.selected.append(location)
        # Selecting after a commit means the previous packet was not enough, so
        # the run goes back to LOCATING and the artifact keeps its identity.
        state.mark_locating()
        self._persist_session()
        return ToolOutput(
            success=True,
            data={
                "status": "COMPLETE",
                "phase": state.phase,
                "selected_count": len(state.selected),
                "symbol_id": symbol_id,
                "file": location.file,
                "start_line": location.start_line,
                "end_line": location.end_line,
                "name": location.name,
                "kind": location.kind,
            },
        )

    def _service_sync_resolve(self, file_path: str) -> None:
        from openjiuwen.core.retrieval.code_graph.service import CodeGraphService

        CodeGraphService(self.context.repo_root, self.context.config).resolve_path(file_path)

    async def _resolve_symbol_evidence(self, symbol_id: str) -> dict[str, Any] | None:
        try:
            service = await self._service()
            payload = await service.resolve_symbol(symbol_id, limit=8)
        except Exception:  # noqa: BLE001 — select degrades to the original guard
            return None
        if not isinstance(payload, dict):
            return None
        matches = payload.get("matches")
        if payload.get("status") == "COMPLETE" and payload.get("symbol_id"):
            return payload
        if isinstance(matches, list) and matches:
            hit = matches[0]
            if isinstance(hit, dict) and hit.get("symbol_id"):
                return hit
        return None


def _candidate_for(state, symbol_id: str) -> dict[str, Any] | None:
    from openjiuwen.core.retrieval.code_graph.query.resolve_symbol import split_file_and_symbol

    if symbol_id in state.candidates:
        return state.candidates[symbol_id]
    file_part, local = split_file_and_symbol(symbol_id)
    if not file_part or not local:
        return None
    wanted = local.lower()
    wanted_file = file_part.replace("\\", "/")
    for sid, payload in state.candidates.items():
        if not isinstance(payload, dict):
            continue
        payload_file = str(payload.get("file") or "").replace("\\", "/")
        if payload_file != wanted_file and wanted_file not in payload_file:
            continue
        name = str(payload.get("name") or "").lower()
        qualified = str(payload.get("qualified_name") or sid).lower()
        if name == wanted or qualified.endswith("." + wanted) or str(sid).endswith("::" + local):
            return payload
    return None


def _has_file_evidence(state, file_path: str) -> bool:
    rel = file_path.replace("\\", "/").lstrip("./")
    if rel in {item.replace("\\", "/") for item in state.seen_files}:
        return True
    if any(str(item.get("file") or "").replace("\\", "/") == rel for item in state.candidates.values()):
        return True
    if any(str(item.get("file") or "").replace("\\", "/") == rel for item in state.read_evidence.values()):
        return True
    return False
