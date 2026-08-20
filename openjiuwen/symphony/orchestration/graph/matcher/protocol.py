"""Request and response protocol for ontology relation matching."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from openjiuwen.symphony.orchestration.graph.models import (
    ALLOWED_RELATION_TYPES,
    GraphDiagnostic,
    LLMMatch,
    RelationCandidate,
    SkillRegistry,
)
from openjiuwen.symphony.shared.fingerprint import FingerprintLike

DEFAULT_THRESHOLDS = {
    "can_feed": 0.7,
}

_MAX_SKILL_DESCRIPTION_LENGTH = 240
_MAX_PORT_DESCRIPTION_LENGTH = 160
_MAX_REASON_LENGTH = 160


def build_match_request(
    registry: SkillRegistry,
    candidates: List[RelationCandidate],
    *,
    reverse_skill_order: bool = False,
) -> Dict[str, Any]:
    indexed = [(f"c{index}", candidate) for index, candidate in enumerate(candidates, start=1)]
    if reverse_skill_order:
        indexed.reverse()
    return {
        "candidates": [_candidate_context(candidate_id, registry, candidate) for candidate_id, candidate in indexed]
    }


def _expand_compact_response(
    payload: Dict[str, Any],
    candidates: List[RelationCandidate],
) -> tuple[Dict[str, Any], List[GraphDiagnostic]]:
    candidates_by_id = {f"c{index}": candidate for index, candidate in enumerate(candidates, start=1)}
    matches = payload.get("matches", [])
    if not isinstance(matches, list):
        return {"matches": matches}, []

    expanded = []
    diagnostics = []
    seen = set()
    for item in matches:
        if not isinstance(item, dict):
            diagnostics.append(
                _protocol_diagnostic(
                    "invalid_match_item",
                    "Compact LLM match item must be an object.",
                )
            )
            continue
        candidate_id = str(item.get("id") or "").strip()
        if candidate_id not in candidates_by_id:
            diagnostics.append(
                _protocol_diagnostic(
                    "unknown_candidate_id",
                    f"Compact LLM match returned unknown candidate id: {candidate_id!r}.",
                )
            )
            continue
        if candidate_id in seen:
            diagnostics.append(
                _protocol_diagnostic(
                    "duplicate_candidate_id",
                    f"Compact LLM match repeated candidate id: {candidate_id!r}.",
                )
            )
            continue
        seen.add(candidate_id)
        candidate = candidates_by_id[candidate_id]
        direction = str(item.get("direction") or "forward").strip().lower()
        if direction not in {"forward", "reverse"}:
            diagnostics.append(
                _protocol_diagnostic(
                    "invalid_candidate_direction",
                    f"Compact LLM match returned invalid direction: {direction!r}.",
                )
            )
            continue
        source_id, target_id = _directed_ids(candidate, direction)
        evidence = _request_directional_evidence(candidate, source_id, target_id)
        if not evidence:
            diagnostics.append(
                _protocol_diagnostic(
                    "unsupported_candidate_direction",
                    f"Candidate {candidate_id!r} has no evidence for {direction}.",
                )
            )
            continue
        reason = str(item.get("reason") or "").strip()[:_MAX_REASON_LENGTH]
        expanded.append(
            {
                "candidate_id": candidate.key,
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": "can_feed",
                "confidence": item.get("confidence", 0),
                "method": "llm_ontology_match",
                "reasons": [reason] if reason else [],
                "supporting_fields": _supporting_fields(evidence),
            }
        )
    return {"matches": expanded}, diagnostics


def parse_match_response(
    payload: Dict[str, Any],
    registry: SkillRegistry,
    candidates: Iterable[RelationCandidate],
    *,
    thresholds: Optional[Dict[str, float]] = None,
) -> tuple[List[LLMMatch], List[GraphDiagnostic]]:
    """Expand, normalize, and validate one compact matcher response."""

    candidate_list = list(candidates)
    _apply_explicit_rejections(payload)
    expanded, protocol_diagnostics = _expand_compact_response(payload, candidate_list)
    matches, validation_diagnostics = _validate_matches(
        expanded,
        registry,
        candidate_list,
        thresholds=thresholds,
    )
    return matches, protocol_diagnostics + validation_diagnostics


def _apply_explicit_rejections(payload: Dict[str, Any]) -> None:
    """A false-like accepted flag must never become truthy by coercion."""

    matches = payload.get("matches")
    if not isinstance(matches, list):
        return
    for item in matches:
        if not isinstance(item, dict) or "accepted" not in item:
            continue
        value = item.get("accepted")
        accepted = value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes"}
        if not accepted:
            item["confidence"] = 0.0


def _candidate_context(
    candidate_id: str,
    registry: SkillRegistry,
    candidate: RelationCandidate,
) -> Dict[str, Any]:
    source = registry.skills[candidate.source_id]
    target = registry.skills[candidate.target_id]
    directions = {}
    for direction, source_id, target_id in (
        ("forward", candidate.source_id, candidate.target_id),
        ("reverse", candidate.target_id, candidate.source_id),
    ):
        evidence = _request_directional_evidence(candidate, source_id, target_id)
        if evidence:
            directions[direction] = _evidence_context(evidence)
    return {
        "id": candidate_id,
        "source": _skill_context(source),
        "target": _skill_context(target),
        "directions": directions,
    }


def _skill_context(skill: FingerprintLike) -> Dict[str, str]:
    return _prune_empty(
        {
            "name": skill.name,
            "description": skill.description[:_MAX_SKILL_DESCRIPTION_LENGTH],
        }
    )


def _evidence_context(evidence: Dict[str, Any]) -> Dict[str, Any]:
    outputs = _compact_fields(evidence.get("source_outputs", []))
    inputs = _compact_fields(evidence.get("target_inputs", []))
    ports = []
    seen = set()
    for mapping in evidence.get("port_mappings", []):
        if not isinstance(mapping, dict):
            continue
        port = _prune_empty(
            {
                "output": mapping.get("source_output"),
                "output_type": mapping.get("source_type"),
                "input": mapping.get("target_input"),
                "input_type": mapping.get("target_type"),
            }
        )
        marker = tuple(sorted(port.items()))
        if not port or marker in seen:
            continue
        seen.add(marker)
        ports.append(port)
    return _prune_empty(
        {
            "outputs": outputs,
            "inputs": inputs,
            "ports": ports,
        }
    )


def _compact_fields(values: Any) -> List[Dict[str, Any]]:
    output = []
    seen = set()
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        field = _prune_empty(
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "required": item.get("required"),
                "description": str(item.get("description") or "")[:_MAX_PORT_DESCRIPTION_LENGTH],
            }
        )
        marker = (
            field.get("name"),
            field.get("type"),
            field.get("required"),
        )
        if marker in seen:
            continue
        seen.add(marker)
        output.append(field)
    return output


def _request_directional_evidence(
    candidate: RelationCandidate,
    source_id: str,
    target_id: str,
) -> Dict[str, Any]:
    directions = candidate.evidence.get("directions", {})
    if isinstance(directions, dict):
        evidence = directions.get(f"{source_id}->{target_id}")
        if isinstance(evidence, dict):
            return evidence
        if "directions" in candidate.evidence:
            return {}
    if source_id == candidate.source_id and target_id == candidate.target_id:
        return candidate.evidence
    return {}


def _directed_ids(
    candidate: RelationCandidate,
    direction: str,
) -> tuple[str, str]:
    if direction == "reverse":
        return candidate.target_id, candidate.source_id
    return candidate.source_id, candidate.target_id


def _supporting_fields(evidence: Dict[str, Any]) -> Dict[str, Any]:
    pairs = []
    source_outputs = set()
    target_inputs = set()
    for mapping in evidence.get("port_mappings", []):
        if not isinstance(mapping, dict):
            continue
        source_output = str(mapping.get("source_output") or "").strip()
        target_input = str(mapping.get("target_input") or "").strip()
        if not source_output or not target_input:
            continue
        pair = {
            "source_output": source_output,
            "target_input": target_input,
        }
        if pair not in pairs:
            pairs.append(pair)
        source_outputs.add(source_output)
        target_inputs.add(target_input)
    if not pairs:
        source_outputs.update(_request_field_names(evidence.get("source_outputs", [])))
        target_inputs.update(_request_field_names(evidence.get("target_inputs", [])))
    return _prune_empty(
        {
            "port_mappings": pairs,
            "source_outputs": sorted(source_outputs),
            "target_inputs": sorted(target_inputs),
        }
    )


def _request_field_names(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    names: List[str] = []
    for item in values:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def _validate_matches(
    payload: Dict[str, Any],
    registry: SkillRegistry,
    candidates: Iterable[RelationCandidate],
    *,
    thresholds: Optional[Dict[str, float]] = None,
) -> tuple[List[LLMMatch], List[GraphDiagnostic]]:
    thresholds = thresholds if thresholds is not None else DEFAULT_THRESHOLDS
    candidate_by_key = {candidate.key: candidate for candidate in candidates}
    candidates_by_pair = {}
    for candidate in candidates:
        candidates_by_pair[(candidate.source_id, candidate.target_id)] = candidate
        candidates_by_pair[(candidate.target_id, candidate.source_id)] = candidate
    matches: List[LLMMatch] = []
    diagnostics: List[GraphDiagnostic] = []

    raw_matches = payload.get("matches", [])
    if not isinstance(raw_matches, list):
        return [], [
            GraphDiagnostic(
                stage="llm_match",
                severity="error",
                code="invalid_matches_payload",
                message="LLM payload field 'matches' must be a list.",
            )
        ]

    for index, raw in enumerate(raw_matches):
        if not isinstance(raw, dict):
            diagnostics.append(
                GraphDiagnostic(
                    stage="llm_match",
                    severity="warning",
                    code="invalid_match_item",
                    message="LLM match item is not an object.",
                    details={"index": index, "item": raw},
                )
            )
            continue

        match, item_diagnostics = _normalize_match(
            raw,
            registry,
            candidate_by_key,
            candidates_by_pair,
            thresholds,
        )
        diagnostics.extend(item_diagnostics)
        if match is not None:
            matches.append(match)

    return matches, diagnostics


def _normalize_match(
    raw: Dict[str, Any],
    registry: SkillRegistry,
    candidate_by_key: Dict[str, RelationCandidate],
    candidates_by_pair: Dict[tuple[str, str], RelationCandidate],
    thresholds: Dict[str, float],
) -> tuple[Optional[LLMMatch], List[GraphDiagnostic]]:
    diagnostics: List[GraphDiagnostic] = []
    source_id = str(raw.get("source_id") or "")
    target_id = str(raw.get("target_id") or "")
    relation_type = str(raw.get("relation_type") or "")
    candidate_id = raw.get("candidate_id")
    candidate_id = str(candidate_id) if candidate_id else None

    errors: List[str] = []
    if source_id not in registry.skills:
        errors.append("source_id does not exist")
    if target_id not in registry.skills:
        errors.append("target_id does not exist")
    if relation_type not in ALLOWED_RELATION_TYPES:
        errors.append("relation_type is not allowed")

    candidate = candidate_by_key.get(candidate_id) if candidate_id else None
    if candidate is None:
        candidate = candidates_by_pair.get((source_id, target_id))
    if candidate is None:
        errors.append("match does not correspond to an input candidate")
    elif relation_type not in candidate.relation_hints:
        errors.append("relation_type is not allowed for the input candidate")

    try:
        confidence = float(raw.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
        errors.append("confidence is not numeric")
    if confidence < 0 or confidence > 1:
        errors.append("confidence must be between 0 and 1")
        confidence = max(0, min(1, confidence))

    reasons = [str(item) for item in raw.get("reasons", []) if str(item).strip()]
    supporting_fields = raw.get("supporting_fields")
    if not isinstance(supporting_fields, dict):
        supporting_fields = {}

    if relation_type == "can_feed" and candidate is not None:
        source_outputs = set(_validation_field_names(supporting_fields.get("source_outputs", [])))
        target_inputs = set(_validation_field_names(supporting_fields.get("target_inputs", [])))
        requested_port_mappings = _port_mapping_pairs(supporting_fields.get("port_mappings", []))
        directional_evidence = _validation_directional_evidence(
            candidate,
            source_id,
            target_id,
        )
        evidence_port_mappings = _port_mapping_pairs(directional_evidence.get("port_mappings", []))
        evidence_outputs = {
            item.get("name") for item in directional_evidence.get("source_outputs", []) if isinstance(item, dict)
        }
        evidence_inputs = {
            item.get("name") for item in directional_evidence.get("target_inputs", []) if isinstance(item, dict)
        }
        if requested_port_mappings:
            if not requested_port_mappings <= evidence_port_mappings:
                errors.append("port_mappings do not match candidate evidence")
            supporting_fields = _complete_supporting_fields_from_port_mappings(
                supporting_fields,
                requested_port_mappings,
            )
        elif source_outputs and target_inputs:
            if not (source_outputs & evidence_outputs and target_inputs & evidence_inputs):
                errors.append("supporting_fields do not match candidate evidence")
        elif not ((evidence_outputs and evidence_inputs) or evidence_port_mappings):
            errors.append("can_feed has no supported output/input pair")

    accepted = not errors and confidence >= thresholds.get(relation_type, 1.0)
    match = LLMMatch(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        confidence=confidence,
        method=str(raw.get("method") or "llm_ontology_match"),
        reasons=reasons,
        supporting_fields=supporting_fields,
        candidate_id=candidate.key if candidate is not None else candidate_id,
        accepted=accepted,
        diagnostics=errors,
        raw=raw,
    )

    if errors:
        diagnostics.append(
            GraphDiagnostic(
                stage="llm_match",
                severity="warning",
                code="rejected_llm_match",
                message="LLM match failed validation.",
                skill_id=source_id or None,
                details={"errors": errors, "match": raw},
            )
        )
    elif not accepted:
        diagnostics.append(
            GraphDiagnostic(
                stage="llm_match",
                severity="info",
                code="low_confidence_llm_match",
                message="LLM match is below the relation threshold.",
                skill_id=source_id,
                details={
                    "threshold": thresholds.get(relation_type),
                    "match": match.to_dict(),
                },
            )
        )

    return match, diagnostics


def _validation_field_names(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    names = []
    for value in values:
        if isinstance(value, str):
            names.append(_field_name_from_string(value))
        elif isinstance(value, dict) and value.get("name"):
            names.append(str(value["name"]))
    return [name for name in names if name]


def _field_name_from_string(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    head = text.split(":", 1)[0].strip()
    return head.split("(", 1)[0].strip()


def _port_mapping_pairs(values: Any) -> set[tuple[str, str]]:
    if not isinstance(values, list):
        return set()
    pairs = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        source_output = str(value.get("source_output") or "").strip()
        target_input = str(value.get("target_input") or "").strip()
        if source_output and target_input:
            pairs.add((source_output, target_input))
    return pairs


def _complete_supporting_fields_from_port_mappings(
    supporting_fields: Dict[str, Any],
    pairs: set[tuple[str, str]],
) -> Dict[str, Any]:
    completed = dict(supporting_fields)
    completed.setdefault("source_outputs", sorted({source for source, _ in pairs}))
    completed.setdefault("target_inputs", sorted({target for _, target in pairs}))
    return completed


def _validation_directional_evidence(
    candidate: RelationCandidate,
    source_id: str,
    target_id: str,
) -> Dict[str, Any]:
    directions = candidate.evidence.get("directions", {})
    if isinstance(directions, dict):
        evidence = directions.get(f"{source_id}->{target_id}")
        if isinstance(evidence, dict):
            return evidence
    return candidate.evidence


def _prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned_mapping = {}
        for key, item in value.items():
            cleaned = _prune_empty(item)
            if cleaned not in (None, "", [], {}):
                cleaned_mapping[key] = cleaned
        return cleaned_mapping
    if isinstance(value, list):
        cleaned_sequence = []
        for item in value:
            cleaned = _prune_empty(item)
            if cleaned not in (None, "", [], {}):
                cleaned_sequence.append(cleaned)
        return cleaned_sequence
    return value


def _protocol_diagnostic(code: str, message: str) -> GraphDiagnostic:
    return GraphDiagnostic(
        stage="llm_match",
        severity="warning",
        code=code,
        message=message,
    )


SYSTEM_PROMPT = """Validate whether each candidate Skill output can feed the target input.

Return JSON only:
{"matches":[{"id":"c1","direction":"forward|reverse","confidence":0.0,"reason":"optional short reason"}]}

Return at most one judgment per candidate id. Use only directions and ports
present in the request. Omit invalid relations or assign low confidence.
Confidence must be between 0 and 1. Keep reason to one short sentence when
useful; do not repeat request data or invent Skills, ports or relations.
"""
