# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared Code Graph finalizer for every termination path."""

from __future__ import annotations

from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path
from openjiuwen.harness.schema.code_graph import (
    CodeGraphLocation,
    CodeGraphResult,
    CodeGraphRunState,
)
from openjiuwen.harness.schema.coding_artifacts import localization_from_result


async def finalize_code_graph_run(
    state: CodeGraphRunState,
    *,
    repo_root: str,
    config: CodeGraphConfig | None = None,
) -> CodeGraphResult:
    """Emit a structured result when the agent stops without submitting context."""
    if state.finished and state.result is not None:
        return state.result
    if state.selected:
        result = _emit(
            state,
            status="PARTIAL",
            summary="stopped without submit_code_context; returning selected locations",
            fallback=False,
        )
        _persist(state, result)
        return result
    seeded = await _seed_top_definition(state, repo_root, config or CodeGraphConfig())
    if seeded is not None:
        state.selected.append(seeded)
        result = _emit(
            state,
            status="PARTIAL",
            summary="stopped without submit_code_context; seeded the top BM25 definition",
            fallback=True,
        )
        _persist(state, result)
        return result
    result = _emit(
        state,
        status="NO_MATCH",
        summary="stopped without submit_code_context; no locations selected",
        fallback=False,
    )
    _persist(state, result)
    return result


def emit_finished_result(
    state: CodeGraphRunState,
    *,
    status: str,
    summary: str,
    open_questions: list[str] | None = None,
    extra_warnings: list[str] | None = None,
    fallback: bool = False,
) -> dict[str, object]:
    result = _emit(
        state,
        status=status,  # type: ignore[arg-type]
        summary=summary,
        fallback=fallback,
        open_questions=open_questions,
        extra_warnings=extra_warnings,
    )
    payload = result.to_dict()
    artifact = localization_from_result(
        result,
        task=state.request.query,
        artifact_id=state.artifact_id or None,
    )
    from openjiuwen.harness.tools.code_graph.session import persist_artifact

    persist_artifact(state, artifact)
    payload["localization_artifact"] = artifact.to_dict()
    return payload


def emit_committed_context(
    state: CodeGraphRunState,
    *,
    status: str,
    summary: str,
    open_questions: list[str] | None = None,
    extra_warnings: list[str] | None = None,
) -> dict[str, object]:
    """Publish the selected spans as a ContextPacket without ending the agent.

    Unlike ``emit_finished_result`` this leaves ``state.finished`` alone: the
    coding agent that produced the packet still has to edit and test. The run
    moves to COMMITTED and may return to LOCATING to refine the same artifact.
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


def _emit(
    state: CodeGraphRunState,
    *,
    status: str,
    summary: str,
    fallback: bool,
    open_questions: list[str] | None = None,
    extra_warnings: list[str] | None = None,
) -> CodeGraphResult:
    result = _build_result(
        state,
        status=status,
        summary=summary,
        fallback=fallback,
        open_questions=open_questions,
        extra_warnings=extra_warnings,
    )
    state.result = result
    state.finished = True
    return result


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
    result = CodeGraphResult(
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
    return result


def _persist(state: CodeGraphRunState, result: CodeGraphResult) -> None:
    artifact = localization_from_result(
        result,
        task=state.request.query,
        artifact_id=state.artifact_id or None,
    )
    from openjiuwen.harness.tools.code_graph.session import persist_artifact

    persist_artifact(state, artifact)
    state.result = result


async def _seed_top_definition(
    state: CodeGraphRunState,
    repo_root: str,
    config: CodeGraphConfig,
) -> CodeGraphLocation | None:
    query = (state.request.query or "").strip()
    if not query:
        return None
    try:
        from openjiuwen.core.retrieval.code_graph.manager import get_code_graph_manager

        service = await get_code_graph_manager().get_service(repo_root, config, ensure=False)
        payload = await service.search_code(query, limit=5)
    except Exception:  # noqa: BLE001 — fallback must not crash invoke
        return None
    if not isinstance(payload, dict):
        return None
    state.remember_payload(payload)
    matches = payload.get("matches") or []
    for item in matches:
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("file") or "")
        if file_path and is_test_path(file_path):
            continue
        symbol_id = str(item.get("symbol_id") or "")
        if not symbol_id:
            continue
        return CodeGraphLocation(
            symbol_id=symbol_id,
            file=file_path,
            start_line=int(item.get("start_line") or 0),
            end_line=int(item.get("end_line") or 0),
            reason="deterministic BM25 fallback seed",
            confidence=0.2,
            name=str(item.get("name") or ""),
            kind=str(item.get("kind") or ""),
        )
    return None
