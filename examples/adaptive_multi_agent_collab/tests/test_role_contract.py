"""Role-output enforcement and finite repairs against recorded failed outputs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from openjiuwen.core.foundation.llm import JsonOutputParser, Model

from examples.adaptive_multi_agent_collab.providers.adapter import OpenJiuwenAgent, validate_payload
from examples.adaptive_multi_agent_collab.config import SafetyConfig
from examples.adaptive_multi_agent_collab.workflows.prompts import (
    PROTOCOL_ID,
    ROLE_CONTRACT,
    ROLE_OUTPUT_CONTRACT,
    ROLE_PROTOCOL_ID,
    ROLE_SCHEMES,
    compose_backend_input,
    protocol_instructions,
    seed_prompt_collection,
)
from examples.adaptive_multi_agent_collab.workflows.repair import (
    REPAIR_DIAGNOSTICS,
    REPAIR_DIAGNOSTIC_ID,
    render_repair,
    repair_variants,
    select_repair_diagnostic,
)
from examples.adaptive_multi_agent_collab.schemas import AgentCallKind, ConversationStatus, RoleOutput, Scheme
from examples.adaptive_multi_agent_collab.tests.test_adapter import LocalModel, LocalResponse, invoke, request
from examples.adaptive_multi_agent_collab.tests.test_runtime import _action, _matrix, _runtime, _state


def recorded_failures():
    return json.loads((Path(__file__).parent / "fixtures/role_output_failures.json").read_text())["rows"]


@pytest.mark.parametrize("scheme", ROLE_SCHEMES)
@pytest.mark.parametrize("backend", ("openai/gpt-4.1-mini", "google/gemini-2.5-flash", "anthropic/claude-sonnet-4.6"))
def test_role_contract_separates_input_evidence_from_generated_fields(scheme, backend):
    incoming = '{"answer":"yes","explanation":"", "query":"fixture", "status":null, "tag":"tag-fixture"}'
    component = f"role:{scheme}"
    prompts = seed_prompt_collection()
    body = prompts.render(component, message_text=incoming)
    protocol = protocol_instructions(component, {"admissible_answers": ("yes", "no")})
    actual = compose_backend_input(backend, protocol, body)
    assert actual[1]["content"].count(incoming) == 1
    assert ROLE_PROTOCOL_ID in actual[0]["content"]
    assert actual[0]["content"].count(ROLE_OUTPUT_CONTRACT) == 1
    assert actual[0]["content"].count(ROLE_CONTRACT) == 1
    assert '"status" ("continue" or "complete")' in ROLE_OUTPUT_CONTRACT
    assert "feedback" in ROLE_OUTPUT_CONTRACT and "no other fields" in ROLE_OUTPUT_CONTRACT
    assert "Do not revise the answer" in ROLE_OUTPUT_CONTRACT
    assert "Input fields are evidence, not an output template" in ROLE_OUTPUT_CONTRACT
    for field in ("answer", "explanation", "query", "message metadata"):
        assert field in ROLE_OUTPUT_CONTRACT
    assert "runtime carries" in ROLE_OUTPUT_CONTRACT
    assert ROLE_OUTPUT_CONTRACT in "\n".join(prompts.components[component].structural_constraints())
    assert ROLE_CONTRACT in "\n".join(prompts.components[component].structural_constraints())


@pytest.mark.parametrize(
    "component", ("initial", "revision:S_self", "revision:S_D", "revision:S_T", "revision:S_V", "judge:S_J", "tie")
)
def test_role_contract_does_not_leak_into_answer_producing_protocols(component):
    protocol = protocol_instructions(component, {"admissible_answers": ("yes", "no")})
    assert PROTOCOL_ID in protocol and ROLE_PROTOCOL_ID not in protocol
    assert ROLE_OUTPUT_CONTRACT not in protocol and "Do not revise the answer" not in protocol
    assert '"answer" first' in protocol and '"explanation" second' in protocol


@pytest.mark.parametrize("status", ("Continue", "COMPLETE", " complete", "continue ", "halt", "", None, True, 1))
def test_role_status_enum_is_exact_without_coercion(status):
    data, error = validate_payload("role", {"status": status, "feedback": "A supported issue."})
    assert data is None and error is not None


@pytest.mark.parametrize(
    "payload",
    (
        {"status": "complete"},
        {"status": "complete", "feedback": ""},
        {"status": "complete", "feedback": " \n "},
        {"status": "complete", "feedback": 12},
        {"status": "complete", "feedback": ["accepted"]},
        {"status": "complete", "feedback": "accepted", "answer": "yes"},
        {"status": "complete", "feedback": "accepted", "explanation": ""},
        {"status": "complete", "feedback": "accepted", "query": "copied"},
        {"status": "complete", "feedback": "accepted", "metadata": {}},
    ),
)
def test_role_exact_keys_and_feedback_type_remain_independent_validation(payload):
    data, error = validate_payload("role", payload)
    assert data is None and error


def test_status_order_and_word_targets_are_guidance_only():
    feedback = "decisive " * 61
    data, error = validate_payload("role", {"feedback": feedback, "status": "continue"})
    assert error is None and data == {"status": "continue", "feedback": feedback.strip()}


@pytest.mark.parametrize("row", recorded_failures(), ids=lambda row: str(row["dispatch_id"]))
def test_both_recorded_gemini_outputs_remain_rejected_after_real_sdk_parsing(row):
    raw = row["raw_output"]
    assert hashlib.sha256(raw.encode()).hexdigest() == row["raw_output_sha256"]
    parsed = asyncio.run(JsonOutputParser().parse(raw))
    assert parsed["status"] == "continue" and "answer" in parsed and "explanation" in parsed
    data, error = validate_payload("role", parsed)
    assert data is None and error == "role output must have exactly status and feedback fields"
    assert "response_format" not in row["request_parameters"]
    assert row["finish_reason"] == "stop" and row["charge"] > 0


def test_schema_category_selects_fixed_component_diagnostic_without_failed_content():
    failure = "bad field " + "UNBOUNDED_PRIVATE_ERROR " * 1000
    role = select_repair_diagnostic(failure, kind="role")
    assert role == REPAIR_DIAGNOSTICS["role-schema"]
    assert role == select_repair_diagnostic("invalid enum", kind="role", context_invalid=True)
    assert "only status" in role and "nonempty feedback" in role
    assert "answer, explanation, query, metadata" in role
    assert "UNBOUNDED_PRIVATE_ERROR" not in role
    for kind in ("initial", "revision", "judge", "tie"):
        value = select_repair_diagnostic(failure, kind=kind)
        assert value == REPAIR_DIAGNOSTICS["schema"]
        assert "only answer and explanation strings" in value and "explanation may be empty" in value
    assert select_repair_diagnostic("output-token-limit", kind="role") == REPAIR_DIAGNOSTICS["truncation"]
    assert select_repair_diagnostic("not a JSON object", kind="role") == REPAIR_DIAGNOSTICS["json"]


def test_component_envelopes_include_exact_applicable_catalogue_and_original_input():
    base = "Complete protected original evidence"
    roles = repair_variants(base, kind="role")
    answers = repair_variants(base, kind="revision")
    assert len(roles) == 4 and len(answers) == 5
    assert roles[0] == answers[0] == base
    assert render_repair(base, REPAIR_DIAGNOSTICS["role-schema"]) in roles
    assert render_repair(base, REPAIR_DIAGNOSTICS["schema"]) not in roles
    assert render_repair(base, REPAIR_DIAGNOSTICS["role-schema"]) not in answers
    for body in {*roles[1:], *answers[1:]}:
        assert body.startswith(base) and body.count(base) == 1
        assert body.count(f"Repair diagnostic ({REPAIR_DIAGNOSTIC_ID})") == 1


def test_standalone_adapter_repairs_role_schema_once_with_original_input_and_raw_receipts():
    rows = recorded_failures()
    responses = [
        LocalResponse(row["raw_output"], asyncio.run(JsonOutputParser().parse(row["raw_output"]))) for row in rows
    ]
    model = LocalModel(responses)
    output = invoke(OpenJiuwenAgent(cast(Model, model), parse_retries=1), request(AgentCallKind.ROLE))
    assert isinstance(output, RoleOutput)
    assert output.validation_exhausted and output.status is ConversationStatus.SAFEGUARD_COMPLETE
    assert len(model.calls) == 2 and output.usage.model_calls == 2
    assert [attempt.raw_output for attempt in output.attempts] == [row["raw_output"] for row in rows]
    assert model.calls[1].messages[-1]["content"] == render_repair("offline prompt", REPAIR_DIAGNOSTICS["role-schema"])
    assert "Gorillinae" not in model.calls[1].messages[-1]["content"]


@pytest.mark.parametrize("scheme", (Scheme.SELF, Scheme.DEBATE, Scheme.TEACHER, Scheme.REVIEWER))
def test_runtime_role_repair_exhaustion_preserves_answer_and_exact_attempt_accounting(scheme):
    receiver = 0 if scheme is Scheme.SELF else 1
    rows = recorded_failures()
    parsed = [asyncio.run(JsonOutputParser().parse(row["raw_output"])) for row in rows]
    active = {(0, receiver): scheme}
    runtime, agents = _runtime(
        2, {receiver: {"role": parsed}}, {1: _matrix(2, active)}, safety=SafetyConfig(parse_retries=1)
    )
    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(_state(2), _action(2, active), {0: "a0", 1: "a1"})
    )
    trace = traces[0]
    assert trace.completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert trace.terminal.answer == "A" and [value.answer for value in raw[0]] == ["A"]
    assert len(trace.messages) == 1 and trace.messages[0].explanation == "a0"
    assert len(agents[receiver].calls) == usage.model_calls == 2
    assert all(call.kind == "role" for agent in agents.values() for call in agent.calls)
    base = agents[receiver].calls[0].prompt
    assert agents[receiver].calls[1].prompt == render_repair(base, REPAIR_DIAGNOSTICS["role-schema"])
    assert sum(record.event == "conversation-complete" for record in history) == 1
    assert trace.outputs[0].usage.model_calls == 2 and len(trace.outputs[0].attempts) == 2
