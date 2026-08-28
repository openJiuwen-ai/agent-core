# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish a selected Code Graph context packet without ending the agent."""

from __future__ import annotations

from openjiuwen.harness.schema.code_graph import (
    CodeGraphResult,
    CodeGraphRunState,
)
from openjiuwen.harness.schema.coding_artifacts import localization_from_result


def emit_committed_context(
    state: CodeGraphRunState,
    *,
    status: str,
    summary: str,
    open_questions: list[str] | None = None,
    extra_warnings: list[str] | None = None,
) -> dict[str, object]:
    """Publish the selected spans as a ContextPacket without ending the agent.

    The coding agent that produced the packet still has to edit and test. The
    run moves to COMMITTED and may return to LOCATING to refine the same artifact.
    """
    result = _build_result(
        state,
        status=status,
        summary=summary,
        fallback=False,
        open_questions=open_questions,
        extra_warnings=extra_warnings,
    )
    state.result = result
    state.mark_committed()
    artifact = localization_from_result(
        result,
        task=state.request.query,
        artifact_id=state.artifact_id or None,
    )
    # Keep the id so a later refinement commits into the same artifact instead
    # of minting a second one for the same task.
    state.artifact_id = artifact.artifact_id
    from openjiuwen.harness.tools.code_graph.session import persist_artifact

    persist_artifact(state, artifact)
    payload = result.to_dict()
    payload["phase"] = state.phase
    payload["context_packet"] = context_packet(state, artifact.artifact_id)
    payload["localization_artifact"] = artifact.to_dict()
    return payload


def context_packet(state: CodeGraphRunState, artifact_id: str) -> dict[str, object]:
    """Compact handoff the patch owner reads before editing."""
    files: dict[str, list[dict[str, object]]] = {}
    for location in state.selected:
        rel = (location.file or "").replace("\\", "/")
        files.setdefault(rel, []).append(
            {
                "symbol_id": location.symbol_id,
                "start_line": location.start_line,
                "end_line": location.end_line,
                "reason": location.reason,
                "confidence": location.confidence,
            }
        )
    return {
        "artifact_id": artifact_id,
        "task": state.request.query,
        "index_snapshot": state.index_snapshot,
        "file_count": len(files),
        "span_count": len(state.selected),
        "files": [{"file": path, "spans": spans} for path, spans in sorted(files.items())],
    }


def _build_result(
    state: CodeGraphRunState,
    *,
    status: str,
    summary: str,
    fallback: bool,
    open_questions: list[str] | None = None,
    extra_warnings: list[str] | None = None,
) -> CodeGraphResult:
    warnings = list(state.warnings)
    if extra_warnings:
        warnings.extend(extra_warnings)
    if fallback and "fallback=true" not in warnings:
        warnings.append("fallback=true")
    return CodeGraphResult(
        status=status,  # type: ignore[arg-type]
        summary=summary,
        locations=list(state.selected),
        relations=list(state.relations),
        open_questions=list(open_questions or []),
        warnings=warnings,
        stats={
            "tool_calls": state.tool_calls,
            "candidate_count": len(state.candidates),
            "selected_count": len(state.selected),
            "index_snapshot": state.index_snapshot,
            "fallback": bool(fallback),
            "profile": state.profile,
        },
    )
