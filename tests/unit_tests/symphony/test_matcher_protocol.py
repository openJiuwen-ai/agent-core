from openjiuwen.symphony import ArtifactSpec, Fingerprint, ParameterSpec
from openjiuwen.symphony.orchestration.graph.matcher.protocol import (
    build_match_request,
    parse_match_response,
)
from openjiuwen.symphony.orchestration.graph.models import RelationCandidate, SkillRegistry


def _registry_and_candidate() -> tuple[SkillRegistry, RelationCandidate]:
    source = Fingerprint(
        type="skill",
        id="source",
        name="Source",
        description="Produces normalized text.",
        version="1.0.0",
        outputs=[ArtifactSpec(name="text", type="text")],
    )
    target = Fingerprint(
        type="skill",
        id="target",
        name="Target",
        description="Consumes normalized text.",
        version="1.0.0",
        inputs=[ParameterSpec(name="text", type="text")],
    )
    candidate = RelationCandidate(
        source_id="source",
        target_id="target",
        relation_hints=["can_feed"],
        candidate_methods=["exact_io_match"],
        priority="high",
        evidence={
            "directions": {
                "source->target": {
                    "source_outputs": [{"name": "text", "type": "text"}],
                    "target_inputs": [{"name": "text", "type": "text"}],
                    "port_mappings": [
                        {
                            "source_output": "text",
                            "source_type": "text",
                            "target_input": "text",
                            "target_type": "text",
                        }
                    ],
                }
            }
        },
    )
    return SkillRegistry(skills={source.id: source, target.id: target}), candidate


def test_build_match_request_uses_compact_candidate_protocol() -> None:
    registry, candidate = _registry_and_candidate()

    request = build_match_request(registry, [candidate])

    assert request["candidates"] == [
        {
            "id": "c1",
            "source": {"name": "Source", "description": "Produces normalized text."},
            "target": {"name": "Target", "description": "Consumes normalized text."},
            "directions": {
                "forward": {
                    "outputs": [{"name": "text", "type": "text"}],
                    "inputs": [{"name": "text", "type": "text"}],
                    "ports": [
                        {
                            "output": "text",
                            "output_type": "text",
                            "input": "text",
                            "input_type": "text",
                        }
                    ],
                }
            },
        }
    ]


def test_parse_match_response_repairs_explicit_false_and_applies_threshold() -> None:
    registry, candidate = _registry_and_candidate()

    rejected, diagnostics = parse_match_response(
        {
            "matches": [
                {
                    "id": "c1",
                    "direction": "forward",
                    "confidence": 0.99,
                    "accepted": "false",
                }
            ]
        },
        registry,
        [candidate],
    )
    below_threshold, _ = parse_match_response(
        {"matches": [{"id": "c1", "direction": "forward", "confidence": 0.69}]},
        registry,
        [candidate],
    )

    assert rejected[0].confidence == 0
    assert rejected[0].accepted is False
    assert diagnostics[-1].code == "low_confidence_llm_match"
    assert below_threshold[0].accepted is False


def test_parse_match_response_reports_unknown_candidate() -> None:
    registry, candidate = _registry_and_candidate()

    matches, diagnostics = parse_match_response(
        {"matches": [{"id": "missing", "direction": "forward", "confidence": 0.9}]},
        registry,
        [candidate],
    )

    assert matches == []
    assert [item.code for item in diagnostics] == ["unknown_candidate_id"]
