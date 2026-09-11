from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
    SymphonyEdgeDecision,
)
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    SymphonyExecutionFragment,
)
from openjiuwen.harness.rails.evolution.symphony_execution_graph import (
    CapabilityIdentity,
    CapabilitySnapshotProvider,
    SymphonyGraphEvolutionSubmission,
    SymphonyGraphObservationSink,
    build_symphony_execution_graph,
    build_symphony_graph_evolution_submission,
)

_TRACE_ID = "1" * 32


class _ExplodingSequence(Sequence[Any]):
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __getitem__(self, index: int) -> Any:
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


class _ExplodingPortsIdentity(CapabilityIdentity):
    def __getattribute__(self, name: str) -> Any:
        if name == "output_ports":
            raise RuntimeError("ports unavailable")
        return super().__getattribute__(name)


class _ExplodingAliasIdentity(CapabilityIdentity):
    def __getattribute__(self, name: str) -> Any:
        if name == "capability_name":
            raise RuntimeError("alias unavailable")
        return super().__getattribute__(name)


class _EvilDict(dict[str, Any]):
    def items(self) -> Any:
        raise RuntimeError("malicious mapping")


class _BaseExplodingDict(dict[str, Any]):
    def items(self) -> Any:
        raise KeyboardInterrupt("system cancellation")


class _MemoryExplodingDict(dict[str, Any]):
    def items(self) -> Any:
        raise MemoryError("memory exhausted")


class _CustomMapping(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


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
    return CapabilityIdentity(
        capability_id=capability_id,
        capability_type=capability_type,  # type: ignore[arg-type]
        capability_name=capability_name,
        version=version,
        content_hash=content_hash or f"sha256:{capability_id}",
        input_ports=input_ports,
        output_ports=output_ports,
    )


def _build(
    candidates: list[SymphonyEdgeCandidate],
    decisions: list[SymphonyEdgeDecision],
    identities: list[CapabilityIdentity],
    *,
    outcome: str = "success",
    reason: str | None = None,
    quality_flags: Sequence[str] = (),
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
    )


def _planned_graph(graph_id: str = "planned-1") -> dict[str, Any]:
    return {
        "graph": {
            "id": graph_id,
            "type": "planned_graph",
            "directed": True,
            "metadata": {"status": "ready"},
            "nodes": {
                "source-id": {"label": "source", "metadata": {"type": "skill"}},
                "target-id": {"label": "target", "metadata": {"type": "tool"}},
            },
            "edges": [{"source": "source-id", "target": "target-id", "relation": "can_feed"}],
        }
    }


def _execution_with_edge() -> dict[str, Any]:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    return _build(
        [candidate],
        [_decision(candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )


def _edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload["graph"]["edges"]


def _submission_hash(submission: SymphonyGraphEvolutionSubmission) -> str:
    canonical = json.dumps(
        {
            "planned_graph": submission.planned_graph,
            "execution_graph": submission.execution_graph,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _refresh_execution_graph_id(execution: dict[str, Any]) -> None:
    graph_without_id = dict(execution["graph"])
    graph_without_id.pop("id", None)
    envelope = {**execution, "graph": graph_without_id}
    canonical = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    execution["graph"]["id"] = "execution_graph:sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    assert _edges(result)[0]["metadata"]["reason"] == "skill3 rejected the artifact"
    assert _edges(result)[1]["metadata"]["success"] is True
    assert "reason" not in _edges(result)[1]["metadata"]
    assert set(result["graph"]["nodes"]) == {"skill-2", "skill-3", "skill-5"}
    assert result["graph"]["nodes"]["skill-2"] == {
        "label": "skill",
        "metadata": {
            "capability_type": "skill",
            "version": "1.0.0",
            "content_hash": "sha256:skill-2",
            "input_ports": ["default_input"],
            "output_ports": ["artifact_uri"],
        },
    }
    assert _edges(result)[0]["metadata"]["port_mappings"] == [
        {"source_output": "artifact_uri", "target_input": "source_uri"}
    ]


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
        ("version", ""),
        ("content_hash", ""),
    ],
)
def test_missing_or_invalid_identity_field_drops_related_edge(field: str, value: str) -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    valid_source = _identity("source-id", "skill", "source")
    invalid_target = replace(_identity("target-id", "tool", "target"), **{field: value})

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


def test_conflicting_metadata_for_same_capability_id_drops_related_edges() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("source-id", "skill", "source", version="1"),
            _identity("source-id", "skill", "source", version="2"),
            _identity("target-id", "tool", "target"),
        ],
    )

    assert _edges(result) == []


def test_valid_and_invalid_records_with_same_capability_id_block_the_id() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    valid_source = _identity("source-id", "skill", "source")
    invalid_source = replace(valid_source, content_hash="")

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
    invalid_alias = replace(valid_source, capability_id="", version="")

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

    assert _edges(valid)[0]["metadata"]["evidence_method"] == "model_assisted"
    assert _edges(valid)[0]["metadata"]["evidence_strength"] == "low"
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


def test_port_mapping_comes_from_the_frozen_capability_snapshot() -> None:
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

    assert _edges(result)[0]["metadata"]["port_mappings"] == [
        {"source_output": "report_uri", "target_input": "document_uri"}
    ]


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
def test_missing_ambiguous_or_invalid_ports_fail_closed(
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

    assert _edges(result) == []


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("input_ports", ["input"]),
        ("output_ports", ["output"]),
        ("input_ports", (" spaced ",)),
        ("output_ports", (42,)),
    ],
)
def test_capability_port_collections_are_strict_immutable_tuples(field_name: str, value: Any) -> None:
    source = replace(_identity("source-id", "skill", "source"), **{field_name: value})
    target = _identity("target-id", "tool", "target")
    candidate = _candidate(1, _fragment(1, "skill", "source"), _fragment(2, "tool", "target"))

    result = _build([candidate], [_decision(candidate)], [source, target])

    assert _edges(result) == []


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("capability_id", "bad\ud800id"),
        ("capability_name", "bad\u200bname"),
        ("version", "1.0\ninvalid"),
        ("content_hash", "sha256:\ud800"),
        ("input_ports", ("bad\u200binput",)),
        ("output_ports", ("bad\x00output",)),
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


def test_surrogate_port_observation_does_not_clear_an_independent_valid_edge() -> None:
    valid = _candidate(1, _fragment(1, "skill", "source"), _fragment(2, "tool", "target"))
    invalid = _candidate(2, _fragment(3, "skill", "bad-source"), _fragment(4, "tool", "bad-target"))

    result = _build(
        [invalid, valid],
        [_decision(invalid), _decision(valid)],
        [
            _identity("source-id", "skill", "source"),
            _identity("target-id", "tool", "target"),
            _identity("bad-source-id", "skill", "bad-source", output_ports=("\ud800",)),
            _identity("bad-target-id", "tool", "bad-target"),
        ],
    )

    assert [edge["metadata"]["candidate_id"] for edge in _edges(result)] == [valid.candidate_id]


def test_exploding_ports_still_poison_the_same_readable_alias() -> None:
    source = _fragment(1, "skill", "shared")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    exploding = _ExplodingPortsIdentity(
        capability_id="bad-source-id",
        capability_type="skill",
        capability_name="shared",
        version="1",
        content_hash="sha256:bad",
        input_ports=("input",),
        output_ports=("output",),
    )

    result = _build(
        [candidate],
        [_decision(candidate)],
        [
            _identity("source-id", "skill", "shared"),
            exploding,
            _identity("target-id", "tool", "target"),
        ],
    )

    assert _edges(result) == []


def test_unreadable_alias_invalidates_the_whole_snapshot_without_escaping() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    unreadable = _ExplodingAliasIdentity(
        capability_id="unreadable-id",
        capability_type="skill",
        capability_name="unreadable",
        version="1",
        content_hash="sha256:unreadable",
        input_ports=("input",),
        output_ports=("output",),
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
        capability_snapshot=[  # type: ignore[list-item]
            _identity("source-id", "skill", "source"),
            _identity("target-id", "tool", "target"),
            object(),
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

    assert [edge["metadata"]["candidate_id"] for edge in _edges(result)] == [valid.candidate_id]


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
    assert [edge["metadata"]["candidate_id"] for edge in _edges(result)] == ["candidate-1", "candidate-2"]
    assert [edge["metadata"]["success"] for edge in _edges(result)] == [True, False]


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


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "missing_graph_id",
        "wrong_graph_type",
        "not_directed",
        "nodes_not_mapping",
        "edges_not_list",
        "invalid_outcome",
        "failed_without_reason",
        "success_with_reason",
        "invalid_trace",
        "query_not_string",
        "invalid_quality_flags",
        "unsorted_quality_flags",
    ],
)
def test_submission_rejects_invalid_execution_envelope(case: str) -> None:
    execution = _build([], [], [])
    if case == "empty":
        execution = {}
    elif case == "missing_graph_id":
        execution["graph"]["id"] = ""
    elif case == "wrong_graph_type":
        execution["graph"]["type"] = "planned_graph"
    elif case == "not_directed":
        execution["graph"]["directed"] = False
    elif case == "nodes_not_mapping":
        execution["graph"]["nodes"] = []
    elif case == "edges_not_list":
        execution["graph"]["edges"] = {}
    elif case == "invalid_outcome":
        execution["outcome"] = "unknown"
    elif case == "failed_without_reason":
        execution["outcome"] = "failed"
    elif case == "success_with_reason":
        execution["reason"] = "must be omitted"
    elif case == "invalid_trace":
        execution["trace_id"] = "bad trace"
    elif case == "query_not_string":
        execution["query"] = 42
    elif case == "invalid_quality_flags":
        execution["quality_flags"] = ["valid", 42]
    elif case == "unsorted_quality_flags":
        execution["quality_flags"] = ["z", "a"]

    if case not in {"empty", "missing_graph_id"}:
        _refresh_execution_graph_id(execution)

    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(None, execution)


@pytest.mark.parametrize(
    "case",
    [
        "missing_endpoint",
        "wrong_relation",
        "missing_metadata",
        "success_not_bool",
        "too_few_evidence_refs",
        "invalid_port_mapping",
        "deterministic_evidence",
        "missing_candidate_ref",
        "missing_fragment_ref",
        "ambiguous_endpoint_ports",
        "failure_without_reason",
    ],
)
def test_submission_rejects_invalid_execution_edge_contract(case: str) -> None:
    execution = _execution_with_edge()
    edge = execution["graph"]["edges"][0]
    if case == "missing_endpoint":
        edge["target"] = "missing-node"
    elif case == "wrong_relation":
        edge["relation"] = "depends_on"
    elif case == "missing_metadata":
        edge.pop("metadata")
    elif case == "success_not_bool":
        edge["metadata"]["success"] = 1
    elif case == "too_few_evidence_refs":
        edge["metadata"]["evidence_refs"] = edge["metadata"]["evidence_refs"][:1]
    elif case == "invalid_port_mapping":
        edge["metadata"]["port_mappings"] = [{"source_output": "", "target_input": "context"}]
    elif case == "deterministic_evidence":
        edge["metadata"]["evidence_method"] = "deterministic"
        edge["metadata"]["evidence_strength"] = "strong"
    elif case == "missing_candidate_ref":
        edge["metadata"]["candidate_id"] = ""
    elif case == "missing_fragment_ref":
        edge["metadata"].pop("target_fragment_id")
    elif case == "ambiguous_endpoint_ports":
        source_id = edge["source"]
        execution["graph"]["nodes"][source_id]["metadata"]["output_ports"].append("another_output")
    elif case == "failure_without_reason":
        edge["metadata"]["success"] = False

    _refresh_execution_graph_id(execution)

    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(None, execution)


def test_submission_rejects_execution_content_with_a_stale_graph_id() -> None:
    execution = _execution_with_edge()
    execution["query"] = "tampered after graph ID generation"

    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(None, execution)


@pytest.mark.parametrize("duplicate_identity", ["candidate", "occurrence_pair"])
def test_submission_rejects_duplicate_execution_edge_identity(duplicate_identity: str) -> None:
    execution = _execution_with_edge()
    duplicate = json.loads(json.dumps(execution["graph"]["edges"][0]))
    if duplicate_identity == "occurrence_pair":
        duplicate["metadata"]["candidate_id"] = "different-candidate"
    execution["graph"]["edges"].append(duplicate)
    _refresh_execution_graph_id(execution)

    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(None, execution)


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "missing_graph_id",
        "wrong_graph_type",
        "not_directed",
        "not_ready",
        "nodes_not_mapping",
        "edges_not_list",
        "missing_endpoint",
        "wrong_relation",
    ],
)
def test_submission_rejects_invalid_planned_envelope(case: str) -> None:
    planned = _planned_graph()
    if case == "empty":
        planned = {}
    elif case == "missing_graph_id":
        planned["graph"]["id"] = ""
    elif case == "wrong_graph_type":
        planned["graph"]["type"] = "execution_graph"
    elif case == "not_directed":
        planned["graph"]["directed"] = False
    elif case == "not_ready":
        planned["graph"]["metadata"]["status"] = "needs_input"
    elif case == "nodes_not_mapping":
        planned["graph"]["nodes"] = []
    elif case == "edges_not_list":
        planned["graph"]["edges"] = {}
    elif case == "missing_endpoint":
        planned["graph"]["edges"][0]["target"] = "missing-node"
    elif case == "wrong_relation":
        planned["graph"]["edges"][0]["relation"] = "depends_on"

    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(planned, _build([], [], []))


@pytest.mark.parametrize("value", [{}, object(), _CustomMapping()])
def test_submission_rejects_arbitrary_execution_objects(value: Any) -> None:
    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(None, value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [object(), _CustomMapping()])
def test_submission_rejects_arbitrary_planned_objects(value: Any) -> None:
    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(value, _build([], [], []))  # type: ignore[arg-type]


def test_capability_identity_and_submission_are_frozen_dataclasses() -> None:
    identity = _identity("skill-id", "skill", "skill")
    submission = build_symphony_graph_evolution_submission(None, _build([], [], []))

    with pytest.raises(FrozenInstanceError):
        identity.version = "2"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        submission.submission_id = "changed"  # type: ignore[misc]


def test_pair_submission_hash_uses_exact_canonical_pair_and_is_order_independent() -> None:
    planned_a = _planned_graph()
    planned_b = {
        "graph": {
            "edges": list(planned_a["graph"]["edges"]),
            "nodes": dict(reversed(list(planned_a["graph"]["nodes"].items()))),
            "metadata": dict(planned_a["graph"]["metadata"]),
            "directed": True,
            "type": "planned_graph",
            "id": "planned-1",
        }
    }
    execution_a = _build([], [], [])
    execution_b = json.loads(json.dumps(execution_a, ensure_ascii=False))

    first = build_symphony_graph_evolution_submission(planned_a, execution_a)
    second = build_symphony_graph_evolution_submission(planned_b, execution_b)
    canonical = json.dumps(
        {"planned_graph": planned_a, "execution_graph": execution_a},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert first.submission_id == second.submission_id == expected
    assert first.planned_graph == second.planned_graph
    assert first.execution_graph == second.execution_graph


def test_pair_submission_isolated_from_later_input_mutation_and_supports_planned_none() -> None:
    planned = _planned_graph()
    execution = _build([], [], [])
    submission = build_symphony_graph_evolution_submission(planned, execution)
    without_planned = build_symphony_graph_evolution_submission(None, execution)

    planned["graph"]["nodes"]["source-id"]["metadata"]["type"] = "mutated"
    execution["graph"]["label"] = "mutated"

    assert submission.planned_graph["graph"]["nodes"]["source-id"]["metadata"]["type"] == "skill"
    assert submission.execution_graph["graph"]["label"] == "capability execution graph"
    assert without_planned.planned_graph is None
    assert without_planned.submission_id.startswith("sha256:")


def test_submission_returns_deeply_isolated_json_views_and_hash_stays_current() -> None:
    source = _fragment(1, "skill", "source")
    target = _fragment(2, "tool", "target")
    candidate = _candidate(1, source, target)
    execution = _build(
        [candidate],
        [_decision(candidate)],
        [_identity("source-id", "skill", "source"), _identity("target-id", "tool", "target")],
    )
    planned = _planned_graph()
    planned["graph"]["nodes"]["source-id"]["metadata"]["version"] = "1"
    submission = build_symphony_graph_evolution_submission(planned, execution)
    original_planned = submission.planned_graph
    original_execution = submission.execution_graph
    original_hash = submission.submission_id

    submission.execution_graph["tampered"] = True
    execution_view = submission.execution_graph
    execution_view["graph"]["nodes"]["source-id"]["metadata"]["version"] = "mutated"
    execution_view["graph"]["edges"].append({"source": "evil", "target": "evil"})
    submission.planned_graph["tampered"] = True
    planned_view = submission.planned_graph
    planned_view["graph"]["nodes"]["source-id"]["metadata"]["version"] = "mutated"
    planned_view["graph"]["edges"].clear()

    assert submission.execution_graph == original_execution
    assert submission.planned_graph == original_planned
    assert submission.execution_graph is not submission.execution_graph
    assert submission.planned_graph is not submission.planned_graph
    assert json.loads(json.dumps(submission.execution_graph)) == original_execution
    assert submission.submission_id == original_hash == _submission_hash(submission)
    assert not hasattr(submission, "__dict__")


def test_submission_public_serialization_api_returns_detached_json() -> None:
    submission = build_symphony_graph_evolution_submission(_planned_graph(), _execution_with_edge())
    payload = submission.to_dict()
    pair_json = submission.canonical_pair_json()
    pair_bytes = submission.canonical_pair_bytes()

    assert payload == {
        "submission_id": submission.submission_id,
        "planned_graph": submission.planned_graph,
        "execution_graph": submission.execution_graph,
    }
    assert pair_bytes == pair_json.encode("utf-8")
    assert json.loads(pair_json) == {
        "planned_graph": submission.planned_graph,
        "execution_graph": submission.execution_graph,
    }

    payload["execution_graph"]["graph"]["edges"].clear()
    payload["planned_graph"]["graph"]["nodes"].clear()
    assert submission.execution_graph["graph"]["edges"]
    assert submission.planned_graph["graph"]["nodes"]
    assert submission.submission_id == _submission_hash(submission)


@pytest.mark.parametrize(
    ("planned", "execution"),
    [
        ({"value": float("nan")}, {}),
        ({"value": {1, 2}}, {}),
        ({1: "non-string-key"}, {}),
        ({"value": ("tuple",)}, {}),
        ({}, {"value": object()}),
    ],
)
def test_pair_submission_rejects_nan_and_non_json_values(planned: dict[Any, Any], execution: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(planned, execution)


def test_pair_submission_rejects_values_that_cannot_be_canonically_encoded_as_utf8() -> None:
    with pytest.raises(ValueError):
        execution = _build([], [], [])
        execution["extra"] = "\ud800"
        build_symphony_graph_evolution_submission(None, execution)


@pytest.mark.parametrize("case", ["circular", "deep", "evil_dict", "custom_mapping", "bytes", "nan"])
def test_submission_normalization_errors_are_uniform_value_errors(case: str) -> None:
    execution = _build([], [], [])
    if case == "circular":
        value: Any = {}
        value["self"] = value
    elif case == "deep":
        value = {}
        cursor = value
        for _ in range(200):
            cursor["next"] = {}
            cursor = cursor["next"]
    elif case == "evil_dict":
        value = _EvilDict(value="secret")
    elif case == "custom_mapping":
        value = _CustomMapping()
    elif case == "bytes":
        value = b"not-json"
    else:
        value = float("nan")
    execution["extra"] = value

    with pytest.raises(ValueError):
        build_symphony_graph_evolution_submission(None, execution)


def test_submission_normalization_does_not_swallow_base_exception() -> None:
    execution = _build([], [], [])
    execution["extra"] = _BaseExplodingDict(value="secret")

    with pytest.raises(KeyboardInterrupt, match="system cancellation"):
        build_symphony_graph_evolution_submission(None, execution)


def test_submission_normalization_preserves_memory_error() -> None:
    execution = _build([], [], [])
    execution["extra"] = _MemoryExplodingDict(value="secret")

    with pytest.raises(MemoryError, match="memory exhausted"):
        build_symphony_graph_evolution_submission(None, execution)


@pytest.mark.asyncio
async def test_snapshot_provider_and_sink_protocols_are_usable_with_fakes() -> None:
    identities = (_identity("skill-id", "skill", "skill"),)

    class FakeProvider:
        def snapshot_capabilities(self) -> tuple[CapabilityIdentity, ...]:
            return identities

    class FakeSink:
        def __init__(self) -> None:
            self.received: list[SymphonyGraphEvolutionSubmission] = []

        async def submit(self, submission: SymphonyGraphEvolutionSubmission) -> None:
            self.received.append(submission)

    provider: CapabilitySnapshotProvider = FakeProvider()
    sink: SymphonyGraphObservationSink = FakeSink()
    assert isinstance(provider, CapabilitySnapshotProvider)
    assert isinstance(sink, SymphonyGraphObservationSink)
    submission = build_symphony_graph_evolution_submission(None, _build([], [], list(provider.snapshot_capabilities())))
    await sink.submit(submission)

    assert sink.received == [submission]  # type: ignore[attr-defined]
