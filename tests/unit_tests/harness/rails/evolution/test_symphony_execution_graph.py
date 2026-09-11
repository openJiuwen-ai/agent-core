from __future__ import annotations

import inspect
import json
from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from typing import Any, overload
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution.symphony_edge_evaluator import (
    SymphonyEdgeEndpointSummary,
    SymphonyEdgeEvaluationSummary,
    evaluate_symphony_edge_candidates,
)
from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
    SymphonyEdgeDecision,
    SymphonyInterruptContinuation,
    build_model_edge_decisions,
    build_symphony_edge_candidates,
)
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    SymphonyExecutionFragment,
    project_symphony_execution_fragments,
)
from openjiuwen.harness.rails.evolution.symphony_execution_graph import (
    CapabilityIdentity,
    _canonical_graph_pair,
    _execution_graph_id,
    build_symphony_execution_graph,
)

_TRACE_ID = "1" * 32


class _ExplodingSequence(Sequence[Any]):
    def __init__(self, error: BaseException) -> None:
        self._error = error

    @overload
    def __getitem__(self, index: int) -> Any: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Any]: ...

    def __getitem__(self, index: int | slice) -> Any:
        del index
        raise self._error

    def __len__(self) -> int:
        return 1

    def __iter__(self) -> Iterator[Any]:
        raise self._error


class _ExplodingCandidate(SymphonyEdgeCandidate):
    def __getattribute__(self, name: str) -> Any:
        if name == "candidate_id":
            raise RuntimeError("candidate property failed")
        return super().__getattribute__(name)


class _MemoryExplodingCandidate(SymphonyEdgeCandidate):
    def __getattribute__(self, name: str) -> Any:
        if name == "candidate_id":
            raise MemoryError("memory exhausted")
        return super().__getattribute__(name)


class _ExplodingObservationCandidate(SymphonyEdgeCandidate):
    def __getattribute__(self, name: str) -> Any:
        if name == "source_fragment":
            raise RuntimeError("candidate observation failed")
        return super().__getattribute__(name)


class _ExplodingAliasIdentity(CapabilityIdentity):
    def __getattribute__(self, name: str) -> Any:
        if name == "capability_name":
            raise RuntimeError("alias unavailable")
        return super().__getattribute__(name)


def _fragment(
    index: int,
    capability_type: str,
    capability_name: str | None,
) -> SymphonyExecutionFragment:
    return SymphonyExecutionFragment(
        fragment_id=f"fragment-{index}",
        capability_type=capability_type,  # type: ignore[arg-type]
        capability_name=capability_name,
        trace_id=_TRACE_ID,
        anchor_span_id=f"{index:016x}",
        branch_span_id="0" * 16,
        span_ids=(f"{index:016x}",),
        continuity_index=0,
    )


def _candidate(
    index: int,
    source: SymphonyExecutionFragment,
    target: SymphonyExecutionFragment,
) -> SymphonyEdgeCandidate:
    return SymphonyEdgeCandidate(
        candidate_id=f"candidate-{index}",
        source_fragment=source,
        target_fragment=target,
        evidence_refs=(
            f"{source.trace_id}#span={source.anchor_span_id}",
            f"{target.trace_id}#span={target.anchor_span_id}",
        ),
        candidate_reasons=("structured_reference",),
    )


def _decision(
    candidate: SymphonyEdgeCandidate,
    status: str = "success",
    *,
    reason: str | None = None,
    evidence_method: str = "model_assisted",
    evidence_strength: str = "low",
    evidence_refs: tuple[str, ...] | None = None,
) -> SymphonyEdgeDecision:
    return SymphonyEdgeDecision(
        candidate_id=candidate.candidate_id,
        source_fragment_id=candidate.source_fragment.fragment_id,
        target_fragment_id=candidate.target_fragment.fragment_id,
        status=status,  # type: ignore[arg-type]
        reason=reason if reason is not None else ("consumer failed" if status == "failure" else "consumed"),
        evidence_refs=candidate.evidence_refs if evidence_refs is None else evidence_refs,
        evidence_method=evidence_method,  # type: ignore[arg-type]
        evidence_strength=evidence_strength,  # type: ignore[arg-type]
    )


def _identity(
    capability_id: str,
    capability_type: str,
    capability_name: str,
    *,
    version: str = "1.0.0",
    content_hash: str | None = None,
    input_ports: tuple[str, ...] = ("default_input",),
    output_ports: tuple[str, ...] = ("default_output",),
) -> CapabilityIdentity:
    del version, content_hash, input_ports, output_ports
    return CapabilityIdentity(
        capability_id=capability_id,
        capability_type=capability_type,  # type: ignore[arg-type]
        capability_name=capability_name,
    )


def _build(
    candidates: list[SymphonyEdgeCandidate],
    decisions: list[SymphonyEdgeDecision],
    identities: list[CapabilityIdentity],
    *,
    outcome: str = "success",
    reason: str | None = None,
    quality_flags: Sequence[str] = (),
    graph_snapshot: dict[str, str] | None = None,
) -> dict[str, Any]:
    return build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="solve the task",
        outcome=outcome,  # type: ignore[arg-type]
        reason=reason,
        candidates=candidates,
        decisions=decisions,
        capability_snapshot=identities,
        quality_flags=quality_flags,
        graph_snapshot=graph_snapshot,
    )


def _edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload["graph"]["edges"]


def test_builds_required_jgf_and_keeps_only_supported_example_edges() -> None:
    skill2 = _fragment(2, "skill", "skill2")
    skill3 = _fragment(3, "skill", "skill3")
    skill5 = _fragment(5, "skill", "skill5")
    failed = _candidate(23, skill2, skill3)
    succeeded = _candidate(25, skill2, skill5)
    unsupported = _candidate(35, skill3, skill5)

    result = _build(
        [unsupported, succeeded, failed],
        [
            _decision(succeeded),
            _decision(unsupported, "insufficient_evidence", evidence_strength="none"),
            _decision(failed, "failure", reason="skill3 rejected the artifact"),
        ],
        [
            _identity("skill-2", "skill", "skill2", output_ports=("artifact_uri",)),
            _identity("skill-3", "skill", "skill3", input_ports=("source_uri",)),
            _identity("skill-5", "skill", "skill5"),
        ],
    )

    assert result["trace_id"] == _TRACE_ID
    assert result["query"] == "solve the task"
    assert result["outcome"] == "success"
    assert "reason" not in result
    assert result["graph"]["type"] == "execution_graph"
    assert result["graph"]["label"] == "capability execution graph"
    assert result["graph"]["directed"] is True
    assert result["graph"]["id"]
    assert [(edge["source"], edge["target"]) for edge in _edges(result)] == [
        ("skill-2", "skill-3"),
        ("skill-2", "skill-5"),
    ]
    assert _edges(result)[0]["relation"] == "can_feed"
    assert _edges(result)[0]["metadata"]["success"] is False
    assert _edges(result)[0]["metadata"] == {"success": False}
    assert _edges(result)[1]["metadata"]["success"] is True
    assert set(result["graph"]["nodes"]) == {"skill-2", "skill-3", "skill-5"}
    assert result["graph"]["nodes"]["skill-2"] == {"label": "skill"}


def test_failed_and_partial_outcomes_require_outer_reason_while_success_omits_it() -> None:
    success = _build([], [], [], outcome="success", reason="must not leak")
    failed = _build([], [], [], outcome="failed", reason="task failed")
    partial_without_reason = _build([], [], [], outcome="partial")

    assert "reason" not in success
    assert failed["reason"] == "task failed"
    assert partial_without_reason == {}


@pytest.mark.parametrize(
    "trace_id",
    ["", "trace with space", "trace#fragment", "trace\nline", "trace\x00control", "\ud800"],
)
def test_execution_builder_rejects_invalid_trace_id(trace_id: str) -> None:
    result = build_symphony_execution_graph(
        trace_id=trace_id,
        query="query",
        outcome="success",
        candidates=[],
        decisions=[],
        capability_snapshot=[],
    )

    assert result == {}


def test_maps_fragment_by_exact_type_and_name_or_exact_type_and_id() -> None:
    by_name = _fragment(1, "skill", "friendly-name")
    by_id = _fragment(2, "tool", "tool-id")
    candidate = _candidate(1, by_name, by_id)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("skill-id", "skill", "friendly-name"),
            _identity("tool-id", "tool", "different-name"),
        ],
    )

    assert [(edge["source"], edge["target"]) for edge in _edges(result)] == [("skill-id", "tool-id")]


def test_same_name_across_types_resolves_by_type() -> None:
    skill = _fragment(1, "skill", "shared")
    tool = _fragment(2, "tool", "shared")
    candidate = _candidate(1, skill, tool)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("skill-id", "skill", "shared"),
            _identity("tool-id", "tool", "shared"),
        ],
    )

    assert [(edge["source"], edge["target"]) for edge in _edges(result)] == [("skill-id", "tool-id")]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability_id", ""),
        ("capability_type", "plugin"),
        ("capability_name", ""),
    ],
)
def test_missing_or_invalid_identity_field_drops_related_edge(field: str, value: str) -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    valid_source = _identity("source-id", "skill", "source")
    invalid_target = replace(_identity("target-id", "tool", "target"), **{field: value})  # type: ignore[arg-type]

    result = _build([candidate], [_decision(candidate)], [valid_source, invalid_target])

    assert _edges(result) == []
    assert result["graph"]["nodes"] == {}


def test_ambiguous_name_or_name_id_collision_drops_edge() -> None:
    source = _fragment(1, "skill", "ambiguous")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("skill-a", "skill", "ambiguous"),
            _identity("skill-b", "skill", "ambiguous"),
            _identity("ambiguous", "skill", "third-name"),
            _identity("tool-id", "tool", "target"),
        ],
    )

    assert _edges(result) == []


def test_duplicate_capability_id_drops_related_edges() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("source-id", "skill", "source"),
            _identity("source-id", "skill", "source"),
            _identity("target-id", "tool", "target"),
        ],
    )

    assert _edges(result) == []


def test_valid_and_invalid_records_with_same_capability_id_block_the_id() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    valid_source = _identity("source-id", "skill", "source")
    invalid_source = replace(valid_source, capability_name="")

    result = _build(
        [candidate],
        [_decision(candidate)],
        [valid_source, invalid_source, _identity("target-id", "tool", "target")],
    )

    assert _edges(result) == []


def test_invalid_record_blocks_a_type_name_alias_shared_with_valid_record() -> None:
    source = _fragment(1, "skill", "shared-name")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    valid_source = _identity("source-id", "skill", "shared-name")
    invalid_alias = replace(valid_source, capability_id="")

    result = _build(
        [candidate],
        [_decision(candidate)],
        [valid_source, invalid_alias, _identity("target-id", "tool", "target")],
    )

    assert _edges(result) == []


@pytest.mark.parametrize(
    "decision_factory",
    [
        lambda candidate: _decision(candidate, "insufficient_evidence", evidence_strength="none"),
        lambda candidate: _decision(candidate, evidence_method="model_assisted", evidence_strength="strong"),
        lambda candidate: _decision(candidate, evidence_method="deterministic", evidence_strength="low"),
        lambda candidate: _decision(candidate, evidence_refs=()),
        lambda candidate: _decision(candidate, evidence_refs=("not-a-span-ref",)),
        lambda candidate: _decision(candidate, "failure", reason=" "),
    ],
)
def test_invalid_decision_contract_drops_edge(decision_factory: Any) -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [decision_factory(candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert _edges(result) == []


def test_model_assisted_low_is_valid_and_evidence_must_be_candidate_allowlisted() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    identities = [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")]

    valid = _build(
        [candidate],
        [_decision(candidate, evidence_method="model_assisted", evidence_strength="low")],
        identities,
    )
    invalid = _build(
        [candidate],
        [
            _decision(
                candidate,
                evidence_method="model_assisted",
                evidence_strength="low",
                evidence_refs=(f"{_TRACE_ID}#span={'f' * 16}",),
            )
        ],
        identities,
    )

    assert _edges(valid)[0]["metadata"] == {"success": True}
    assert _edges(invalid) == []


def test_deterministic_decision_is_not_execution_evidence() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate, evidence_method="deterministic", evidence_strength="strong")],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert _edges(result) == []


def test_multiple_ports_do_not_block_a_valid_execution_edge() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("source-id", "skill", "source", output_ports=("report_uri",)),
            _identity("target-id", "tool", "target", input_ports=("document_uri",)),
        ],
    )

    assert _edges(result)[0]["metadata"] == {"success": True}


@pytest.mark.parametrize(
    ("source_ports", "target_ports"),
    [
        ((), ("document_uri",)),
        (("report_uri",), ()),
        (("report_uri", "summary"), ("document_uri",)),
        (("report_uri",), ("document_uri", "context")),
        (("report_uri", "report_uri"), ("document_uri",)),
        (("report_uri",), ("document_uri", "document_uri")),
        (("",), ("document_uri",)),
    ],
)
def test_ports_are_not_part_of_execution_identity(
    source_ports: tuple[str, ...],
    target_ports: tuple[str, ...],
) -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("source-id", "skill", "source", output_ports=source_ports),
            _identity("target-id", "tool", "target", input_ports=target_ports),
        ],
    )

    assert len(_edges(result)) == 1


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("capability_id", "bad\ud800id"),
        ("capability_name", "bad\u200bname"),
    ],
)
def test_capability_identity_text_rejects_invalid_utf8_and_control_characters(
    field_name: str,
    value: Any,
) -> None:
    source = replace(_identity("source-id", "skill", "source"), **{field_name: value})
    target = _identity("target-id", "tool", "target")
    candidate = _candidate(1, _fragment(1, "skill", "source"), _fragment(2, "tool", "target"))

    result = _build([candidate], [_decision(candidate)], [source, target])

    assert _edges(result) == []


def test_invalid_identity_does_not_clear_an_independent_valid_edge() -> None:
    valid = _candidate(1, _fragment(1, "skill", "source"), _fragment(2, "tool", "target"))
    invalid = _candidate(2, _fragment(3, "skill", "bad-source"), _fragment(4, "tool", "bad-target"))

    result = _build(
        [invalid, valid],
        [_decision(invalid), _decision(valid)],
        [
            _identity("source-id", "skill", "source"),
            _identity("target-id", "tool", "target"),
            _identity("bad-source-id", "skill", "bad\ud800source"),
            _identity("bad-target-id", "tool", "bad-target"),
        ],
    )

    assert [(edge["source"], edge["target"]) for edge in _edges(result)] == [("source-id", "target-id")]


def test_unreadable_alias_invalidates_the_whole_snapshot_without_escaping() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    unreadable = _ExplodingAliasIdentity(
        capability_id="unreadable-id",
        capability_type="skill",
        capability_name="unreadable",
    )

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("source-id", "skill", "source"),
            _identity("target-id", "tool", "target"),
            unreadable,
        ],
    )

    assert _edges(result) == []


def test_identity_without_readable_alias_fields_invalidates_the_whole_snapshot() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="query",
        outcome="success",
        candidates=[candidate],
        decisions=[_decision(candidate)],
        capability_snapshot=[
            _identity("source-id", "skill", "source"),
            _identity("target-id", "tool", "target"),
            object(),  # type: ignore[list-item]
        ],
    )

    assert _edges(result) == []


def test_candidate_decision_mismatch_duplicate_or_unknown_id_fails_closed() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    mismatched = replace(_decision(candidate), source_fragment_id="different")
    unknown = replace(_decision(candidate), candidate_id="unknown")
    identities = [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")]

    mismatch_result = _build([candidate], [mismatched], identities)
    duplicate_candidate_result = _build([candidate, candidate], [_decision(candidate)], identities)
    duplicate_decision_result = _build([candidate], [_decision(candidate), _decision(candidate)], identities)
    unknown_result = _build([candidate], [unknown], identities)

    assert _edges(mismatch_result) == []
    assert _edges(duplicate_candidate_result) == []
    assert _edges(duplicate_decision_result) == []
    assert _edges(unknown_result) == []


def test_malformed_runtime_candidate_and_decision_fields_do_not_raise() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    malformed_candidate = replace(candidate, candidate_id=[])  # type: ignore[arg-type]
    malformed_decision = replace(_decision(candidate), evidence_method=[])  # type: ignore[arg-type]
    malformed_identity = replace(_identity("target-id", "tool", "target"), capability_type=[])  # type: ignore[arg-type]
    identities = [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")]

    candidate_result = _build([malformed_candidate], [_decision(candidate)], identities)
    decision_result = _build([candidate], [malformed_decision], identities)
    identity_result = _build([candidate], [_decision(candidate)], [identities[0], malformed_identity])
    outcome_result = _build([], [], [], outcome=[])  # type: ignore[arg-type]

    assert _edges(candidate_result) == []
    assert _edges(decision_result) == []
    assert _edges(identity_result) == []
    assert outcome_result == {}


@pytest.mark.parametrize("failing_input", ["candidates", "decisions", "snapshot"])
def test_runtime_sequence_errors_return_valid_empty_execution_graph(failing_input: str) -> None:
    values: dict[str, Any] = {
        "candidates": [],
        "decisions": [],
        "capability_snapshot": [],
    }
    values["capability_snapshot" if failing_input == "snapshot" else failing_input] = _ExplodingSequence(
        RuntimeError("sequence failed")
    )

    result = build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="query",
        outcome="success",
        **values,
    )

    assert result["graph"]["id"]
    assert result["graph"]["type"] == "execution_graph"
    assert result["graph"]["nodes"] == {}
    assert result["graph"]["edges"] == []


def test_runtime_candidate_property_error_returns_valid_empty_execution_graph() -> None:
    base = _candidate(1, _fragment(1, "skill", "source"), _fragment(2, "tool", "target"))
    exploding = _ExplodingObservationCandidate(
        candidate_id=base.candidate_id,
        source_fragment=base.source_fragment,
        target_fragment=base.target_fragment,
        evidence_refs=base.evidence_refs,
        candidate_reasons=base.candidate_reasons,
    )

    result = build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="query",
        outcome="success",
        candidates=[exploding],
        decisions=[_decision(base)],
        capability_snapshot=[],
    )

    assert result["graph"]["nodes"] == {}
    assert result["graph"]["edges"] == []


def test_malformed_candidate_does_not_clear_an_independent_valid_observation() -> None:
    valid = _candidate(1, _fragment(1, "skill", "source"), _fragment(2, "tool", "target"))
    bad_base = _candidate(2, _fragment(3, "skill", "bad-source"), _fragment(4, "tool", "bad-target"))
    exploding = _ExplodingCandidate(
        candidate_id=bad_base.candidate_id,
        source_fragment=bad_base.source_fragment,
        target_fragment=bad_base.target_fragment,
        evidence_refs=bad_base.evidence_refs,
        candidate_reasons=bad_base.candidate_reasons,
    )

    result = _build(
        [exploding, valid],
        [_decision(bad_base), _decision(valid)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert [(edge["source"], edge["target"]) for edge in _edges(result)] == [("source-id", "target-id")]


@pytest.mark.parametrize("failing_input", ["candidates", "decisions", "snapshot"])
def test_runtime_sequences_do_not_swallow_memory_error(failing_input: str) -> None:
    values: dict[str, Any] = {
        "candidates": [],
        "decisions": [],
        "capability_snapshot": [],
    }
    values["capability_snapshot" if failing_input == "snapshot" else failing_input] = _ExplodingSequence(
        MemoryError("memory exhausted")
    )

    with pytest.raises(MemoryError, match="memory exhausted"):
        build_symphony_execution_graph(
            trace_id=_TRACE_ID,
            query="query",
            outcome="success",
            **values,
        )


def test_runtime_candidate_does_not_swallow_memory_error() -> None:
    base = _candidate(1, _fragment(1, "skill", "source"), _fragment(2, "tool", "target"))
    exploding = _MemoryExplodingCandidate(
        candidate_id=base.candidate_id,
        source_fragment=base.source_fragment,
        target_fragment=base.target_fragment,
        evidence_refs=base.evidence_refs,
        candidate_reasons=base.candidate_reasons,
    )

    with pytest.raises(MemoryError, match="memory exhausted"):
        _build([exploding], [_decision(base)], [])


def test_quality_flag_sequence_does_not_swallow_memory_error() -> None:
    with pytest.raises(MemoryError, match="memory exhausted"):
        build_symphony_execution_graph(
            trace_id=_TRACE_ID,
            query="query",
            outcome="success",
            candidates=[],
            decisions=[],
            capability_snapshot=[],
            quality_flags=_ExplodingSequence(MemoryError("memory exhausted")),  # type: ignore[arg-type]
        )


def test_runtime_sequence_does_not_swallow_base_exception() -> None:
    with pytest.raises(KeyboardInterrupt, match="system cancellation"):
        build_symphony_execution_graph(
            trace_id=_TRACE_ID,
            query="query",
            outcome="success",
            candidates=_ExplodingSequence(KeyboardInterrupt("system cancellation")),  # type: ignore[arg-type]
            decisions=[],
            capability_snapshot=[],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", ""),
        ("fragment_id", ""),
        ("anchor_span_id", ""),
        ("branch_span_id", ""),
        ("continuity_index", True),
        ("continuity_index", "0"),
        ("span_ids", []),
        ("span_ids", ()),
        ("span_ids", ("",)),
        ("span_ids", ("f" * 16,)),
    ],
)
def test_incomplete_fragment_occurrence_identity_drops_edge(field: str, value: Any) -> None:
    source = replace(_fragment(1, "skill", "source"), **{field: value})
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert _edges(result) == []


def test_nodes_include_only_edge_endpoints() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("source-id", "skill", "source"),
            _identity("target-id", "tool", "target"),
            _identity("unused-id", "subagent", "unused"),
        ],
    )

    assert set(result["graph"]["nodes"]) == {"source-id", "target-id"}


def test_clean_trace_can_build_deterministic_empty_graph() -> None:
    first = _build([], [], [_identity("unused-id", "skill", "unused")])
    second = _build([], [], [])

    assert first["graph"]["edges"] == []
    assert first["graph"]["nodes"] == {}
    assert first == second
    assert first["graph"]["id"].startswith("execution_graph:sha256:")


def test_edges_are_stably_sorted_and_cycle_is_retained() -> None:
    first = _fragment(1, "skill", "first")
    second = _fragment(2, "tool", "second")
    forward = _candidate(2, first, second)
    backward = _candidate(1, second, first)
    identities = [_identity("z-source", "skill", "first"), _identity("a-target", "tool", "second")]

    result_a = _build([forward, backward], [_decision(forward), _decision(backward)], identities)
    result_b = _build([backward, forward], [_decision(backward), _decision(forward)], list(reversed(identities)))

    assert result_a == result_b
    assert [(edge["source"], edge["target"]) for edge in _edges(result_a)] == [
        ("a-target", "z-source"),
        ("z-source", "a-target"),
    ]


def test_same_capability_pair_success_and_failure_observations_are_both_retained() -> None:
    source_one = _fragment(1, "skill", "source")
    target_one = _fragment(2, "tool", "target")
    source_two = replace(source_one, fragment_id="fragment-3", anchor_span_id="3" * 16, span_ids=("3" * 16,))
    target_two = replace(target_one, fragment_id="fragment-4", anchor_span_id="4" * 16, span_ids=("4" * 16,))
    succeeded = _candidate(1, source_one, target_one)
    failed = _candidate(2, source_two, target_two)

    result = _build(
        [failed, succeeded],
        [_decision(failed, "failure", reason="retryable handoff failure"), _decision(succeeded)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert len(_edges(result)) == 2
    assert [edge["metadata"]["success"] for edge in _edges(result)] == [False, True]


def test_parallel_branch_observations_are_retained_independently() -> None:
    first_source = replace(_fragment(1, "skill", "first"), branch_span_id="a" * 16)
    second_source = replace(_fragment(2, "skill", "second"), branch_span_id="b" * 16)
    target = _fragment(3, "tool", "merge")
    first_edge = _candidate(1, first_source, target)
    second_edge = _candidate(2, second_source, target)

    result = _build(
        [second_edge, first_edge],
        [_decision(second_edge), _decision(first_edge)],
        [
            _identity("first-id", "skill", "first"),
            _identity("second-id", "skill", "second"),
            _identity("merge-id", "tool", "merge"),
        ],
    )

    assert [(edge["source"], edge["target"]) for edge in _edges(result)] == [
        ("first-id", "merge-id"),
        ("second-id", "merge-id"),
    ]


def test_same_occurrence_self_loop_is_dropped() -> None:
    occurrence = _fragment(1, "skill", "same-name")
    candidate = _candidate(1, occurrence, occurrence)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [_identity("same-id", "skill", "same-name")],
    )

    assert _edges(result) == []


def test_forged_fragment_ids_cannot_hide_same_occurrence() -> None:
    source = replace(
        _fragment(1, "skill", "source"),
        span_ids=("1".zfill(16), "3".zfill(16)),
    )
    target = replace(
        _fragment(2, "tool", "target"),
        fragment_id="different-fragment-id",
        anchor_span_id=source.anchor_span_id,
        span_ids=(source.anchor_span_id, "4".zfill(16)),
    )
    candidate = replace(
        _candidate(1, source, target),
        evidence_refs=(
            f"{_TRACE_ID}#span={source.anchor_span_id}",
            f"{_TRACE_ID}#span={'3'.zfill(16)}",
        ),
    )

    result = _build(
        [candidate],
        [_decision(candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert _edges(result) == []


def test_duplicate_fragment_id_does_not_merge_distinct_occurrences() -> None:
    source = _fragment(1, "skill", "source")
    target = replace(_fragment(2, "tool", "target"), fragment_id=source.fragment_id)
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert [(edge["source"], edge["target"]) for edge in _edges(result)] == [("source-id", "target-id")]


def test_distinct_occurrences_of_same_capability_may_form_capability_self_loop() -> None:
    first = _fragment(1, "skill", "same-id")
    second = _fragment(2, "skill", "same-name")
    candidate = _candidate(1, first, second)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [_identity("same-id", "skill", "same-name")],
    )

    assert [(edge["source"], edge["target"]) for edge in _edges(result)] == [("same-id", "same-id")]


def test_query_and_outcome_do_not_change_edge_observations() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    identities = [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")]
    decisions = [_decision(candidate)]

    success = _build([candidate], decisions, identities)
    partial = build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="different query",
        outcome="partial",
        reason="only part completed",
        candidates=[candidate],
        decisions=decisions,
        capability_snapshot=identities,
    )

    assert _edges(success) == _edges(partial)


@pytest.mark.parametrize("violation", ["trace", "continuity"])
def test_endpoint_trace_and_continuity_must_match(violation: str) -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    if violation == "trace":
        target = replace(target, trace_id="2" * 32)
    else:
        target = replace(target, continuity_index=1)
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert _edges(result) == []


def test_foreign_same_trace_evidence_span_drops_edge() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    foreign_ref = f"{_TRACE_ID}#span={'f' * 16}"
    candidate = replace(_candidate(1, source, target), evidence_refs=(foreign_ref,))

    result = _build(
        [candidate],
        [_decision(candidate, evidence_refs=(foreign_ref,))],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert _edges(result) == []


def test_single_reference_or_single_endpoint_evidence_drops_edge() -> None:
    source = replace(_fragment(1, "skill", "source"), span_ids=("1".zfill(16), "3".zfill(16)))
    target = _fragment(2, "tool", "target")
    base = _candidate(1, source, target)
    source_refs = (
        f"{_TRACE_ID}#span={'1'.zfill(16)}",
        f"{_TRACE_ID}#span={'3'.zfill(16)}",
    )
    single_ref_candidate = replace(base, evidence_refs=(source_refs[0],))
    single_endpoint_candidate = replace(base, candidate_id="candidate-2", evidence_refs=source_refs)

    single_ref = _build(
        [single_ref_candidate],
        [_decision(single_ref_candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )
    single_endpoint = _build(
        [single_endpoint_candidate],
        [_decision(single_endpoint_candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )

    assert _edges(single_ref) == []
    assert _edges(single_endpoint) == []


def test_overlapping_windows_require_each_occurrence_anchor_reference() -> None:
    shared_span = "3".zfill(16)
    source = replace(_fragment(1, "skill", "source"), span_ids=("1".zfill(16), shared_span))
    target = replace(_fragment(2, "tool", "target"), span_ids=("2".zfill(16), shared_span))
    base = _candidate(1, source, target)
    shared_ref = f"{_TRACE_ID}#span={shared_span}"
    source_ref = f"{_TRACE_ID}#span={'1'.zfill(16)}"
    insufficient = replace(base, evidence_refs=(shared_ref,))
    target_ref = f"{_TRACE_ID}#span={'2'.zfill(16)}"
    wrong_anchors = replace(base, candidate_id="candidate-2", evidence_refs=(shared_ref, source_ref))
    sufficient = replace(base, candidate_id="candidate-3", evidence_refs=(source_ref, target_ref))
    identities = [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")]

    insufficient_result = _build([insufficient], [_decision(insufficient)], identities)
    wrong_anchors_result = _build([wrong_anchors], [_decision(wrong_anchors)], identities)
    sufficient_result = _build([sufficient], [_decision(sufficient)], identities)

    assert _edges(insufficient_result) == []
    assert _edges(wrong_anchors_result) == []
    assert len(_edges(sufficient_result)) == 1


def test_quality_flags_are_normalized_and_part_of_stable_graph_identity() -> None:
    first = _build([], [], [], quality_flags=(" truncated_trace ", "", "malformed_payload", "truncated_trace"))
    second = _build([], [], [], quality_flags=("malformed_payload", "truncated_trace"))

    assert first["quality_flags"] == ["malformed_payload", "truncated_trace"]
    assert first == second


def test_invalid_quality_flags_do_not_become_stringified_metadata() -> None:
    result = build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="query",
        outcome="success",
        candidates=[],
        decisions=[],
        capability_snapshot=[],
        quality_flags=("valid", object()),  # type: ignore[arg-type]
    )

    assert result["quality_flags"] == ["valid"]


def test_builder_signature_and_behavior_are_independent_of_planned_graph() -> None:
    parameters = inspect.signature(build_symphony_execution_graph).parameters

    assert "planned_graph" not in parameters
    assert _build([], [], [])["graph"]["type"] == "execution_graph"


def test_builder_freezes_invoke_start_graph_snapshot_into_hashed_envelope() -> None:
    snapshot = {
        "static_revision": "static-start",
        "observation_revision": "observation-start",
        "merged_revision": "merged-start",
    }

    result = _build([], [], [], graph_snapshot=snapshot)
    snapshot["static_revision"] = "mutated"

    assert result["graph_snapshot"]["static_revision"] == "static-start"
    assert result["graph"]["id"].startswith("execution_graph:sha256:")


def test_callback_boundary_rejects_stale_execution_graph_id() -> None:
    execution = _build([], [], [])
    execution["graph"]["label"] = "tampered after hashing"

    with pytest.raises(ValueError, match="invalid Symphony graph submission"):
        _canonical_graph_pair(None, execution)


def test_callback_boundary_rejects_illegal_edge_metadata() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    execution = _build(
        [candidate],
        [_decision(candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )
    execution["graph"]["edges"][0]["metadata"]["success"] = 1
    graph_without_id = dict(execution["graph"])
    graph_without_id.pop("id")
    execution["graph"]["id"] = _execution_graph_id({**execution, "graph": graph_without_id})

    with pytest.raises(ValueError, match="invalid Symphony graph submission"):
        _canonical_graph_pair(None, execution)


@pytest.mark.parametrize("invalid", [float("nan"), object()])
def test_callback_boundary_rejects_non_finite_and_non_json_values(invalid: Any) -> None:
    execution = _build([], [], [])
    execution["extra"] = invalid

    with pytest.raises(ValueError):
        _canonical_graph_pair(None, execution)


def test_callback_boundary_rejects_recursive_json() -> None:
    execution = _build([], [], [])
    recursive: dict[str, Any] = {}
    recursive["self"] = recursive
    execution["extra"] = recursive

    with pytest.raises(ValueError):
        _canonical_graph_pair(None, execution)


def _native_cross_trace_input():
    trajectories = []
    for trace, name in ((_TRACE_ID, "producer"), ("2" * 32, "consumer")):
        spans = [
            {
                "traceId": trace,
                "spanId": "0" * 15 + "1",
                "name": "agent.root",
                "startTimeUnixNano": "1",
                "endTimeUnixNano": "5",
            },
            {
                "traceId": trace,
                "spanId": "0" * 15 + "2",
                "parentSpanId": "0" * 15 + "1",
                "name": "tool.skill_tool",
                "startTimeUnixNano": "2",
                "endTimeUnixNano": "3",
                "attributes": attributes_from_map(
                    {
                        semconv.GEN_AI_TOOL_NAME: "skill_tool",
                        semconv.GEN_AI_TOOL_INPUT: json.dumps({"skill_name": name, "relative_file_path": "SKILL.md"}),
                        semconv.GEN_AI_TOOL_OUTPUT: json.dumps({"success": True, "artifact_id": "artifact"}),
                    }
                ),
            },
        ]
        trajectories.append(
            Trajectory.from_otlp(
                {
                    "resourceSpans": [
                        {
                            "resource": {
                                "attributes": attributes_from_map(
                                    {TRAJECTORY_ID: "interrupt-chain", SESSION_ID: "session"}
                                )
                            },
                            "scopeSpans": [{"spans": spans}],
                        }
                    ]
                }
            )
        )
    continuities = tuple((0, trajectory) for trajectory in trajectories)
    fragments = project_symphony_execution_fragments(continuities)
    plan = {
        "graph": {
            "id": "plan",
            "type": "planned_graph",
            "directed": True,
            "metadata": {"status": "ready"},
            "nodes": {name: {"label": name, "metadata": {"type": "skill"}} for name in ("producer", "consumer")},
            "edges": [{"source": "producer", "target": "consumer", "relation": "can_feed", "metadata": {}}],
        }
    }
    continuation = SymphonyInterruptContinuation(_TRACE_ID, "2" * 32, 0, 0, 1, (_TRACE_ID, "2" * 32))
    candidates = build_symphony_edge_candidates(
        fragments, continuities, planned_graph=plan, interrupt_continuations=(continuation,)
    )
    assert len(candidates) == 1
    return plan, continuation, candidates


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["success", "failure", "no_relation", "invalid"])
async def test_native_cross_trace_candidates_through_model_and_graph(status: str) -> None:
    plan, continuation, candidates = _native_cross_trace_input()
    candidate = candidates[0]

    async def judge(messages, **kwargs):
        del kwargs
        payload = json.loads(messages[1]["content"])
        assert set(payload) == {"task", "source", "target"}
        assert "evidence_refs" not in json.dumps(payload)
        if status == "invalid":
            return "invalid JSON"
        return json.dumps({"status": status, "reason": "consumer used the producer artifact"})

    model = SimpleNamespace(invoke=AsyncMock(side_effect=judge))
    decisions = await evaluate_symphony_edge_candidates(
        llm=model,
        query="original task",
        candidates=candidates,
        decisions=build_model_edge_decisions(candidates),
        summaries={
            candidate.candidate_id: SymphonyEdgeEvaluationSummary(
                endpoint_a=SymphonyEdgeEndpointSummary(output="artifact produced"),
                endpoint_b=SymphonyEdgeEndpointSummary(input="artifact consumed"),
            )
        },
    )
    assert model.invoke.await_count == 1
    assert decisions[0].status == ("insufficient_evidence" if status == "invalid" else status)
    identities = [_identity(name, "skill", name) for name in ("producer", "consumer")]
    args = dict(
        trace_id=_TRACE_ID,
        query="original task",
        outcome="success",
        candidates=candidates,
        decisions=decisions,
        capability_snapshot=identities,
        trace_ids=continuation.trace_ids,
        interrupt_continuations=(continuation,),
    )
    graph = build_symphony_execution_graph(**args)
    assert graph["trace_ids"] == list(continuation.trace_ids)
    assert json.loads(_canonical_graph_pair(plan, graph)) == {"planned_graph": plan, "execution_graph": graph}
    assert len(graph["graph"]["edges"]) == (1 if status in {"success", "failure"} else 0)
    if status in {"success", "failure"}:
        edge = graph["graph"]["edges"][0]["metadata"]
        assert edge == {"success": status == "success"}
        for tamper in ("id", "trace_ids"):
            altered = deepcopy(graph)
            if tamper == "id":
                altered["graph"]["id"] = "forged"
            else:
                altered["trace_ids"] = ["2" * 32, _TRACE_ID]
            if tamper != "id":
                altered["graph"]["id"] = _execution_graph_id(altered)
            with pytest.raises(ValueError):
                _canonical_graph_pair(plan, altered)
    # Trace order is part of graph identity even when a segment has no edge.
    first_order = build_symphony_execution_graph(**{**args, "trace_ids": (_TRACE_ID, "2" * 32, "3" * 32, "4" * 32)})
    second_order = build_symphony_execution_graph(**{**args, "trace_ids": (_TRACE_ID, "2" * 32, "4" * 32, "3" * 32)})
    assert first_order["graph"]["id"] != second_order["graph"]["id"]


@pytest.mark.parametrize(
    "case",
    ["missing", "wrong_order", "wrong_continuity", "malformed", "bool", "negative", "out_of_range", "forged_ref"],
)
def test_execution_graph_rejects_invalid_cross_trace_descriptor_or_ref(case: str) -> None:
    _, continuation, candidates = _native_cross_trace_input()
    changes = {
        "wrong_continuity": {"continuity_index": 1},
        "bool": {"source_segment_index": False},
        "negative": {"source_segment_index": -1, "target_segment_index": 0},
        "out_of_range": {"source_segment_index": 2, "target_segment_index": 3},
    }
    boundaries = (continuation,)
    traces = continuation.trace_ids
    if case == "missing":
        boundaries = ()
    elif case == "malformed":
        boundaries = ({"source_trace_id": _TRACE_ID},)
    elif case == "wrong_order":
        traces = (_TRACE_ID, "3" * 32, "2" * 32)
    elif case == "forged_ref":
        candidates = (
            replace(candidates[0], evidence_refs=(_TRACE_ID + "#span=unknown", candidates[0].evidence_refs[1])),
        )
    else:
        changed = replace(continuation, **changes[case])
        boundaries = (changed,)
        candidates = (replace(candidates[0], interrupt_continuation=changed),)
    graph = build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="task",
        outcome="success",
        candidates=candidates,
        decisions=(_decision(candidates[0]),),
        capability_snapshot=[_identity(name, "skill", name) for name in ("producer", "consumer")],
        trace_ids=traces,
        interrupt_continuations=boundaries,
    )
    assert graph["graph"]["edges"] == []


def test_cross_trace_anchors_with_equal_span_ids_cannot_borrow_source_refs() -> None:
    _, continuation, candidates = _native_cross_trace_input()
    candidate = candidates[0]
    source = replace(candidate.source_fragment, span_ids=("0000000000000002", "0000000000000003"))
    refs = (_TRACE_ID + "#span=0000000000000002", _TRACE_ID + "#span=0000000000000003")
    forged = replace(candidate, source_fragment=source, evidence_refs=refs)
    graph = build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="task",
        outcome="success",
        candidates=(forged,),
        decisions=(_decision(forged),),
        capability_snapshot=[_identity(name, "skill", name) for name in ("producer", "consumer")],
        trace_ids=continuation.trace_ids,
        interrupt_continuations=(continuation,),
    )
    assert graph["graph"]["edges"] == []


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_continuation_cannot_turn_existing_candidate_decision_into_edge(reverse: bool) -> None:
    _, boundary, candidates = _native_cross_trace_input()
    conflict = replace(boundary, trace_ids=boundary.trace_ids + ("3" * 32,))
    descriptors = (boundary, conflict) if not reverse else (conflict, boundary)
    graph = build_symphony_execution_graph(
        trace_id=_TRACE_ID,
        query="task",
        outcome="success",
        candidates=candidates,
        decisions=(_decision(candidates[0]),),
        capability_snapshot=[_identity(name, "skill", name) for name in ("producer", "consumer")],
        trace_ids=conflict.trace_ids,
        interrupt_continuations=descriptors,
    )
    assert graph["graph"]["edges"] == []


def test_capability_identity_is_a_frozen_dataclass() -> None:
    identity = _identity("skill-id", "skill", "skill")

    with pytest.raises(FrozenInstanceError):
        identity.version = "2"  # type: ignore[misc]
