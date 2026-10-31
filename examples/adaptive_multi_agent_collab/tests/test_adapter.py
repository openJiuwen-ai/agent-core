"""Offline tests for the public-openJiuwen adapter boundary."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.core.foundation.llm import JsonOutputParser

from examples.adaptive_multi_agent_collab.providers.adapter import (
    DeterministicFakeAgent,
    OpenJiuwenAgent,
    usage_to_dict,
    validate_payload,
)
from examples.adaptive_multi_agent_collab.workflows.repair import REPAIR_DIAGNOSTICS, render_repair
from examples.adaptive_multi_agent_collab.schemas import (
    AgentCallKind,
    AgentOutput,
    AgentRequest,
    ConversationStatus,
    JudgeOutput,
    ParsedOutput,
    RoleOutput,
    TieOutput,
)


@dataclass
class LocalResponse:
    content: str
    parser_content: Any = None
    usage_metadata: Any = None


@dataclass
class LocalCall:
    messages: Any
    output_parser: Any
    timeout: float


class LocalModel:
    """A Model-shaped local object that performs no I/O."""

    def __init__(self, steps: Sequence[LocalResponse | BaseException]) -> None:
        self.steps = list(steps)
        self.calls: list[LocalCall] = []

    async def invoke(self, *, messages: Any, output_parser: Any, timeout: float) -> LocalResponse:
        self.calls.append(LocalCall(messages, output_parser, timeout))
        if not self.steps:
            raise AssertionError("local model script exhausted")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def request(
    kind: AgentCallKind,
    *,
    prompt: str = "offline prompt",
    context: Mapping[str, Any] | None = None,
) -> AgentRequest:
    return AgentRequest(kind, 0, prompt, f"component:{kind.value}", context=context or {})


def invoke(agent: OpenJiuwenAgent | DeterministicFakeAgent, value: AgentRequest) -> ParsedOutput:
    return asyncio.run(agent.invoke(value))


@pytest.mark.parametrize(
    ("kind", "payload", "output_type", "expected"),
    [
        (
            AgentCallKind.INITIAL,
            {"answer": "A", "explanation": "initial evidence"},
            AgentOutput,
            ("A", "initial evidence"),
        ),
        (
            AgentCallKind.REVISION,
            {"answer": "B", "explanation": "changed after review"},
            AgentOutput,
            ("B", "changed after review"),
        ),
        (
            AgentCallKind.ROLE,
            {"status": "continue", "feedback": "check the premise"},
            RoleOutput,
            (ConversationStatus.CONTINUE, "check the premise"),
        ),
        (
            AgentCallKind.JUDGE,
            {"answer": "B", "explanation": "submission two is supported"},
            JudgeOutput,
            ("B", "submission two is supported"),
        ),
    ],
)
def test_public_openjiuwen_adapter_parses_valid_structured_outputs(
    kind: AgentCallKind,
    payload: dict[str, str],
    output_type: type[ParsedOutput],
    expected: tuple[Any, str],
) -> None:
    model = LocalModel(
        [
            LocalResponse(
                content="provider raw content",
                parser_content=payload,
                usage_metadata={
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "total_cost": 0.03,
                    "model_name": "local-model",
                },
            )
        ]
    )
    output = invoke(OpenJiuwenAgent(model), request(kind))  # type: ignore[arg-type]

    assert isinstance(output, output_type)
    if isinstance(output, RoleOutput):
        assert (output.status, output.feedback) == expected
    else:
        assert (output.answer, output.explanation) == expected
    assert output.raw_content == "provider raw content"
    if kind is AgentCallKind.REVISION:
        assert output.structured_output == {"answer": "B", "explanation": "changed after review"}
    else:
        assert output.structured_output == payload
    assert output.usage.total_tokens == 6
    assert output.usage.model_calls == 1
    assert output.usage.paid_monetary_cost == pytest.approx(0.03)
    assert output.usage.model_name == "local-model"
    assert output.error is None
    assert len(model.calls) == 1
    assert isinstance(model.calls[0].output_parser, JsonOutputParser)
    assert model.calls[0].messages[-1] == {"role": "user", "content": "offline prompt"}


def test_malformed_output_retries_with_repair_prompt_and_cumulative_usage() -> None:
    model = LocalModel(
        [
            LocalResponse(
                content="not json",
                usage_metadata={
                    "input_tokens": 3,
                    "output_tokens": 1,
                    "total_cost": 0.10,
                    "model_name": "local-model",
                },
            ),
            LocalResponse(
                content='{"answer":"A","explanation":"fixed"}',
                usage_metadata={
                    "input_tokens": 8,
                    "output_tokens": 2,
                    "total_cost": 0.20,
                    "model_name": "local-model",
                },
            ),
        ]
    )
    output = invoke(OpenJiuwenAgent(model, parse_retries=1), request(AgentCallKind.INITIAL))  # type: ignore[arg-type]

    assert isinstance(output, AgentOutput)
    assert (output.answer, output.explanation) == ("A", "fixed")
    assert output.usage.input_tokens == 11
    assert output.usage.output_tokens == 3
    assert output.usage.total_tokens == 14
    assert output.usage.model_calls == 2
    assert output.usage.paid_monetary_cost == pytest.approx(0.30)
    assert len(output.usage.raw_metadata["attempts"]) == 2
    assert output.parse_errors == ("response is not a JSON object",)
    assert [attempt.raw_output for attempt in output.attempts] == [
        "not json",
        '{"answer":"A","explanation":"fixed"}',
    ]
    assert output.attempts[0].validation_errors == ("response is not a JSON object",)
    assert output.attempts[1].validation_errors == ()
    assert output.usage.total_tokens == sum(attempt.usage.total_tokens for attempt in output.attempts)
    assert len(model.calls) == 2
    assert model.calls[0].messages[-1] == {"role": "user", "content": "offline prompt"}
    assert model.calls[1].messages[-1]["content"] == render_repair("offline prompt", REPAIR_DIAGNOSTICS["json"])
    assert "not json" not in model.calls[1].messages[-1]["content"]


def test_request_zero_repair_override_prevents_nested_adapter_retries() -> None:
    model = LocalModel([LocalResponse("not json", usage_metadata={"input_tokens": 2, "output_tokens": 1})])
    constrained = AgentRequest(
        AgentCallKind.JUDGE,
        0,
        "offline prompt",
        "component:judge",
        max_repair_generations=0,
    )
    output = invoke(OpenJiuwenAgent(model, parse_retries=3), constrained)  # type: ignore[arg-type]

    assert isinstance(output, JudgeOutput)
    assert output.validation_exhausted
    assert output.usage.model_calls == 1
    assert len(output.attempts) == 1
    assert len(model.calls) == 1


class ProviderFailure(RuntimeError):
    """Provider-neutral local exception used by the offline adapter fixture."""


def test_provider_exception_preserves_neutral_type_and_message_without_provider_logic() -> None:
    model = LocalModel([ProviderFailure("quota temporarily unavailable")])
    output = invoke(OpenJiuwenAgent(model, parse_retries=2), request(AgentCallKind.INITIAL))  # type: ignore[arg-type]

    assert isinstance(output, AgentOutput)
    assert output.answer == ""
    assert output.error == "ProviderFailure: quota temporarily unavailable"
    assert output.usage.model_calls == 1
    assert output.usage.total_tokens is None
    assert len(model.calls) == 1


def test_exception_after_parse_failure_counts_both_attempts_and_prior_usage() -> None:
    model = LocalModel(
        [
            LocalResponse("not json", usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_cost": 0.4}),
            ProviderFailure("second attempt failed"),
        ]
    )
    output = invoke(OpenJiuwenAgent(model, parse_retries=1), request(AgentCallKind.INITIAL))  # type: ignore[arg-type]

    assert output.error == "ProviderFailure: second attempt failed"
    assert output.usage.model_calls == 2
    assert output.usage.total_tokens is None
    assert output.usage.monetary_cost is None
    assert output.attempts[0].usage.total_tokens == 3
    assert output.attempts[0].usage.paid_monetary_cost == pytest.approx(0.4)
    assert len(output.usage.raw_metadata["attempts"]) == 2
    assert len(output.attempts) == 2
    assert output.attempts[1].validation_errors == ("ProviderFailure: second attempt failed",)


def test_malformed_role_response_is_marked_as_exhausted_safeguard_output() -> None:
    model = LocalModel([LocalResponse("malformed role", usage_metadata={"input_tokens": 1})])
    output = invoke(OpenJiuwenAgent(model, parse_retries=0), request(AgentCallKind.ROLE))  # type: ignore[arg-type]

    assert isinstance(output, RoleOutput)
    assert output.status is ConversationStatus.SAFEGUARD_COMPLETE
    assert output.feedback == "response is not a JSON object"
    assert output.error == "response is not a JSON object"
    assert output.usage.model_calls == 1
    assert output.validation_exhausted
    assert output.attempts[0].raw_output == "malformed role"


def test_adapter_retains_context_invalid_tie_attempt_for_runtime_validation() -> None:
    model = LocalModel([LocalResponse("raw", {"answer": "C", "explanation": "unsupported selection"})])
    output = invoke(
        OpenJiuwenAgent(model),  # type: ignore[arg-type]
        replace(
            request(AgentCallKind.TIE, context={"tied_answers": ("A", "B"), "repair_base_prompt": "offline prompt"}),
            max_repair_generations=0,
        ),
    )

    assert isinstance(output, TieOutput)
    assert output.answer == "C"
    assert output.tied_answers == ("A", "B")
    assert output.explanation == "unsupported selection"
    assert output.error is None
    assert not output.validation_exhausted
    assert output.attempts[0].structured_output == {
        "answer": "C",
        "explanation": "unsupported selection",
    }


def test_fake_agent_consumes_repeated_script_entries_in_fifo_order() -> None:
    fake = DeterministicFakeAgent(
        0,
        {
            "initial": [
                {"answer": "A", "explanation": "first"},
                {"answer": "B", "explanation": "second"},
            ]
        },
    )

    first = invoke(fake, request(AgentCallKind.INITIAL, prompt="first prompt"))
    second = invoke(fake, request(AgentCallKind.INITIAL, prompt="second prompt"))

    assert isinstance(first, AgentOutput) and isinstance(second, AgentOutput)
    assert (first.answer, second.answer) == ("A", "B")
    assert [call.prompt for call in fake.calls] == ["first prompt", "second prompt"]
    assert fake.active == 0
    assert fake.max_active == 1


def test_fake_agent_role_default_terminates_safely() -> None:
    output = invoke(DeterministicFakeAgent(2), request(AgentCallKind.ROLE))

    assert isinstance(output, RoleOutput)
    assert output.status is ConversationStatus.COMPLETE
    assert output.feedback == "No further change is needed."
    assert output.usage.model_calls == 1
    assert output.usage.raw_metadata["last"]["synthetic"] is True


def test_fake_agent_active_counter_observes_local_overlap_and_returns_to_zero() -> None:
    fake = DeterministicFakeAgent(
        0,
        {"initial": [{"answer": str(index), "explanation": ""} for index in range(3)]},
        latency=0.01,
    )

    async def exercise() -> list[Any]:
        return await asyncio.gather(
            *[fake.invoke(request(AgentCallKind.INITIAL, prompt=f"prompt {index}")) for index in range(3)]
        )

    outputs = asyncio.run(exercise())
    assert [output.answer for output in outputs] == ["0", "1", "2"]
    assert fake.max_active == 3
    assert fake.active == 0


def test_fake_agent_prefers_tag_and_round_specific_script_key() -> None:
    fake = DeterministicFakeAgent(
        0,
        {
            "initial": {"answer": "generic", "explanation": ""},
            "initial:g:2": {"answer": "specific", "explanation": ""},
        },
    )
    specific = invoke(
        fake,
        request(AgentCallKind.INITIAL, context={"tag": "g", "round": 2}),
    )
    generic = invoke(fake, request(AgentCallKind.INITIAL))

    assert isinstance(specific, AgentOutput) and isinstance(generic, AgentOutput)
    assert (specific.answer, generic.answer) == ("specific", "generic")


class UsageObject:
    input_tokens = 2
    output_tokens = 3
    total_cost = 0.4
    code = 0


class DumpableUsage:
    def model_dump(self) -> dict[str, Any]:
        return {"input_tokens": 5, "output_tokens": 8, "total_cost": 0.9}


def test_usage_conversion_preserves_mappings_objects_dumpable_values_and_none() -> None:
    mapping = {"input_tokens": 1, "zero": 0}
    assert usage_to_dict(mapping) == mapping
    assert usage_to_dict(UsageObject()) == {
        "code": 0,
        "input_tokens": 2,
        "output_tokens": 3,
        "total_cost": 0.4,
    }
    assert usage_to_dict(DumpableUsage()) == {
        "input_tokens": 5,
        "output_tokens": 8,
        "total_cost": 0.9,
    }
    assert usage_to_dict(None) is None


@pytest.mark.parametrize(
    ("kind", "payload", "error"),
    [
        ("initial", "not-json", "response is not a JSON object"),
        ("initial", {}, "answer is required"),
        ("role", {"status": "maybe", "feedback": "x"}, "status must be"),
        ("role", {"status": "complete", "feedback": ""}, "feedback is required"),
        ("revision", {"answer": "A"}, "explanation is required"),
        ("judge", {"answer": 1, "explanation": "x"}, "answer is required"),
        ("tie", {"answer": "A", "explanation": None}, "explanation is required"),
    ],
)
def test_payload_validation_reports_explicit_parse_contract_errors(
    kind: str,
    payload: Any,
    error: str,
) -> None:
    data, failure = validate_payload(kind, payload)
    assert data is None
    assert error in (failure or "")


def test_target_python_has_no_forbidden_direct_provider_sdk_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = {"openai", "anthropic", "google", "langchain", "autogen"}
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".", 1)[0].lower() in forbidden:
                    violations.append(f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}:{name}")
    assert violations == []


@pytest.mark.parametrize("supports_seed", [False, True])
def test_public_model_boundary_forwards_only_declared_seed_capability(supports_seed: bool) -> None:
    class SeedModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.model_config = {"temperature": 0.2}

        async def invoke(self, **kwargs: Any) -> LocalResponse:
            self.calls.append(kwargs)
            return LocalResponse('{"answer":"A","explanation":"seeded answer"}')

    model = SeedModel()
    adapter = OpenJiuwenAgent(model, supports_seed=supports_seed)  # type: ignore[arg-type]
    output = invoke(
        adapter,
        request(
            AgentCallKind.INITIAL,
            context={"generation_seed": 0, "logical_call": {"agent_id": 0, "repair_attempt": 0}},
        ),
    )
    assert ("seed" in model.calls[0]) is supports_seed
    if supports_seed:
        assert model.calls[0]["seed"] == 0
    descriptor = output.attempts[0].usage.raw_metadata["generation"]
    assert descriptor["generation_seed"] == 0
    assert descriptor["seed_forwarded"] is supports_seed
    assert ("limitation" in descriptor) is not supports_seed
    assert model.model_config == {"temperature": 0.2}


def test_standalone_adapter_repairs_record_distinct_derived_seeds_and_failed_attempt() -> None:
    class RepairModel:
        def __init__(self) -> None:
            self.seeds: list[int] = []

        async def invoke(self, **kwargs: Any) -> LocalResponse:
            self.seeds.append(kwargs["seed"])
            if len(self.seeds) == 1:
                return LocalResponse("malformed")
            raise RuntimeError("provider failure")

    model = RepairModel()
    adapter = OpenJiuwenAgent(model, supports_seed=True, parse_retries=1)  # type: ignore[arg-type]
    output = invoke(adapter, request(AgentCallKind.ROLE, context={"generation_seed": 19}))
    assert len(model.seeds) == len(set(model.seeds)) == 2
    assert model.seeds[0] == 19
    assert output.usage.model_calls == 2
    assert output.attempts[1].usage.raw_metadata["generation"]["generation_seed"] == model.seeds[1]
    assert "provider failure" in output.error


def test_repairs_never_embed_failed_raw_outputs_or_accumulate_previous_diagnostics() -> None:
    first = "RAW_FAILURE_MARKER " * 3000
    second = '{"answer": "A"}'
    model = LocalModel([LocalResponse(first), LocalResponse(second), LocalResponse('{"answer":"A","explanation":""}')])
    output = invoke(OpenJiuwenAgent(model, parse_retries=2), request(AgentCallKind.INITIAL))  # type: ignore[arg-type]
    assert isinstance(output, AgentOutput) and output.answer == "A"
    bodies = [call.messages[-1]["content"] for call in model.calls]
    assert bodies == [
        "offline prompt",
        render_repair("offline prompt", REPAIR_DIAGNOSTICS["json"]),
        render_repair("offline prompt", REPAIR_DIAGNOSTICS["schema"]),
    ]
    assert "RAW_FAILURE_MARKER" not in "\n".join(bodies)
    assert [attempt.raw_output for attempt in output.attempts] == [first, second, '{"answer":"A","explanation":""}']
    assert all(call.messages[0]["content"].count("Fixed protocol:") == 1 for call in model.calls)


def test_standalone_context_repair_canonicalizes_without_using_correctness() -> None:
    model = LocalModel(
        [
            LocalResponse('{"answer":"I think yes","explanation":"RAW_ADMISSIBLE_MARKER"}'),
            LocalResponse('{"answer":" No. ","explanation":"accepted contrary answer"}'),
        ]
    )
    output = invoke(
        OpenJiuwenAgent(model),
        request(  # type: ignore[arg-type]
            AgentCallKind.INITIAL,
            context={"admissible_answers": ("yes", "no"), "gold_answer": "yes"},
        ),
    )
    assert isinstance(output, AgentOutput) and output.answer == "no" and len(model.calls) == 2
    assert model.calls[1].messages[-1]["content"] == render_repair(
        "offline prompt", REPAIR_DIAGNOSTICS["admissible-answer"]
    )
    assert "RAW_ADMISSIBLE_MARKER" not in model.calls[1].messages[-1]["content"]
    assert "gold_answer" not in str(model.calls)


def test_standalone_tie_uses_its_existing_repair_budget_for_membership() -> None:
    model = LocalModel(
        [LocalResponse('{"answer":"C","explanation":"not a member"}'), LocalResponse('{"answer":"B","explanation":""}')]
    )
    output = invoke(OpenJiuwenAgent(model), request(AgentCallKind.TIE, context={"tied_answers": ("A", "B")}))  # type: ignore[arg-type]
    assert isinstance(output, TieOutput) and output.answer == "B" and len(model.calls) == 2
    assert output.attempts[0].structured_output is not None
    assert output.attempts[0].structured_output["answer"] == "C"


def test_truncation_repair_requests_shorter_fresh_json_without_continuing_raw_text() -> None:
    model = LocalModel(
        [
            LocalResponse('{"answer":"A","explanation":"TRUNCATED_MARKER', usage_metadata={"finish_reason": "length"}),
            LocalResponse('{"answer":"A","explanation":"short"}'),
        ]
    )
    output = invoke(OpenJiuwenAgent(model), request(AgentCallKind.INITIAL))  # type: ignore[arg-type]
    assert isinstance(output, AgentOutput) and output.answer == "A"
    body = model.calls[1].messages[-1]["content"]
    assert body == render_repair("offline prompt", REPAIR_DIAGNOSTICS["truncation"])
    assert "shorter" in body and "do not continue" in body and "TRUNCATED_MARKER" not in body
    assert output.attempts[0].raw_output.endswith("TRUNCATED_MARKER")


def test_workflow_word_count_and_json_key_order_are_guidance_only() -> None:
    explanation = "decisive " * 61
    model = LocalModel([LocalResponse("", {"explanation": explanation, "answer": "A"})])
    output = invoke(OpenJiuwenAgent(model), request(AgentCallKind.INITIAL))  # type: ignore[arg-type]
    assert isinstance(output, AgentOutput) and output.explanation == explanation.strip()
    assert not output.parse_errors and len(model.calls) == 1
