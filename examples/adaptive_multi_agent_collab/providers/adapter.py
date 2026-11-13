"""Agent adapters for the collaboration example.

The production adapter deliberately uses the public openJiuwen ``Model`` API
directly. The workflow runtime stays local because the specification needs access to
the exact queues, tags, usage records, and gradient boundaries.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from openjiuwen.core.foundation.llm import JsonOutputParser, Model

from .resources import ResourceController, ResourceFailure
from ..workflows.prompts import compose_backend_input, protocol_instructions
from ..workflows.repair import REPAIR_DIAGNOSTIC_ID, render_repair, repair_variants, select_repair_diagnostic
from .output_contract import ROLE_OUTPUT_PROTOCOL, SCHEMA_COUNTING_ID, response_format_for

from ..schemas import (
    AgentCallKind,
    AgentOutput,
    AgentRequest,
    ConversationStatus,
    JudgeOutput,
    QueryExample,
    RoleOutput,
    StrategyQAAnswerCanonicalizer,
    TieOutput,
    TrimmedAnswerCanonicalizer,
    UsageRecord,
    ValidationAttempt,
)


@dataclass(slots=True)
class GenerationRequest:
    """One typed model invocation requested by the runtime."""

    kind: str
    prompt: str
    agent_id: int
    context: dict[str, Any] = field(default_factory=dict)
    max_repair_generations: int | None = None


@dataclass(slots=True)
class GenerationResult:
    """Lossless invocation result plus locally validated structured data."""

    content: str
    data: dict[str, Any] | None
    usage_metadata: dict[str, Any] | None
    wall_seconds: float
    attempts: int = 1
    attempt_usage_metadata: list[dict[str, Any] | None] = field(default_factory=list)
    error: str | None = None
    parse_errors: list[str] = field(default_factory=list)
    attempt_records: list[ValidationAttempt] = field(default_factory=list)
    validation_exhausted: bool = False


class AgentBackend(Protocol):
    """Asynchronous generation boundary used by every collaboration agent."""

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


def usage_to_dict(value: Any) -> dict[str, Any] | None:
    """Preserve provider usage metadata, including meaningful zero values."""

    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    fields = (
        "code",
        "err_msg",
        "prompt",
        "task_id",
        "model_name",
        "total_latency",
        "first_token_time",
        "request_start_time",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_tokens",
        "cache_write_tokens",
        "input_cost",
        "output_cost",
        "total_cost",
        "reference_cost",
        "reasoning_tokens",
        "generation_id",
        "served_model",
        "served_provider",
    )
    return {name: getattr(value, name) for name in fields if hasattr(value, name)}


def _payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def validate_payload(kind: str, value: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Validate the minimal structured contract for a prompt component."""

    data = _payload(value)
    if data is None:
        return None, "response is not a JSON object"
    if kind in {"initial", "revision", "judge", "tie"}:
        if not isinstance(data.get("answer"), str) or not data["answer"].strip():
            return None, "answer is required and must be a non-empty string"
        if not isinstance(data.get("explanation"), str):
            return None, "explanation is required and must be a string (empty means unavailable)"
        if set(data) != {"answer", "explanation"}:
            return None, "output must have exactly answer and explanation string fields"
        return {
            "answer": data["answer"].strip(),
            "explanation": data["explanation"].strip(),
        }, None
    if kind == "role":
        if not isinstance(data.get("status"), str):
            return None, "status is required and must be a string"
        if not isinstance(data.get("feedback"), str) or not data["feedback"].strip():
            return None, "feedback is required and must be a non-empty string"
        status = data["status"]
        feedback = data["feedback"].strip()
        if status not in {"continue", "complete"}:
            return None, "status must be 'continue' or 'complete'"
        if set(data) != {"status", "feedback"}:
            return None, "role output must have exactly status and feedback fields"
        return {"status": status, "feedback": feedback}, None
    return data, None


def _validate_answer_context(
    request: GenerationRequest, data: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Standalone answer validation; runtime-owned calls use its single budget."""

    if request.kind == "role" or (request.max_repair_generations == 0 and "repair_base_prompt" in request.context):
        return data, None
    key = {"judge": "allowed_submitted_answers", "tie": "tied_answers"}.get(request.kind, "admissible_answers")
    allowed = request.context.get(key)
    candidates = request.context.get("admissible_answers", allowed)
    if not candidates:
        # An explicitly empty finite selection cannot produce a valid member;
        # generic/open-answer fixtures without a supplied finite space retain
        # their schema-only behavior.
        return (None, "answer selection has no admissible candidates") if allowed is not None else (data, None)
    example = QueryExample("standalone-validation", request.prompt, tuple(candidates))
    canonicalizer = (
        StrategyQAAnswerCanonicalizer() if set(example.candidates) <= {"yes", "no"} else TrimmedAnswerCanonicalizer()
    )
    try:
        answer = canonicalizer.canonicalize(data["answer"], example)
        if answer not in example.candidates or (allowed is not None and answer not in allowed):
            return None, "answer is outside the applicable admissible selection"
    except ValueError as error:
        return None, str(error)
    return {**data, "answer": answer}, None


def _attempt_usage(metadata: dict[str, Any] | None, wall_seconds: float) -> UsageRecord:
    value = metadata or {}
    return UsageRecord(
        input_tokens=int(value["input_tokens"]) if value.get("input_tokens") is not None else None,
        output_tokens=int(value["output_tokens"]) if value.get("output_tokens") is not None else None,
        model_calls=int(value.get("model_calls", 1)),
        monetary_cost=float(value["reference_cost"]) if value.get("reference_cost") is not None else None,
        paid_monetary_cost=float(value["total_cost"]) if value.get("total_cost") is not None else None,
        wall_seconds=wall_seconds,
        model_name=str(value["model_name"]) if value.get("model_name") else None,
        raw_metadata=value,
    )


def _usage_record(result: GenerationResult) -> UsageRecord:
    attempts = result.attempt_usage_metadata or [result.usage_metadata]
    aggregate = sum((_attempt_usage(metadata, 0.0) for metadata in attempts), UsageRecord())
    return UsageRecord(
        input_tokens=aggregate.input_tokens,
        output_tokens=aggregate.output_tokens,
        model_calls=aggregate.model_calls,
        monetary_cost=aggregate.monetary_cost,
        paid_monetary_cost=aggregate.paid_monetary_cost,
        wall_seconds=result.wall_seconds,
        model_name=aggregate.model_name,
        raw_metadata={
            "attempts": attempts,
            "last": result.usage_metadata,
            "effective_input": (result.usage_metadata or {}).get("effective_input"),
        },
    )


def parsed_output(request: AgentRequest, result: GenerationResult):
    data = result.data or {}
    common: dict[str, Any] = {
        "raw_content": result.content,
        "structured_output": data or None,
        "usage": _usage_record(result),
        "error": result.error,
        "prompt_component": request.prompt_component,
        "parse_errors": tuple(result.parse_errors),
        "attempts": tuple(result.attempt_records),
        "validation_exhausted": result.validation_exhausted,
    }
    if request.kind is AgentCallKind.ROLE:
        status = data.get("status", ConversationStatus.SAFEGUARD_COMPLETE.value)
        return RoleOutput(
            feedback=str(data.get("feedback", result.error or "Malformed response; terminate safely.")),
            status=(
                ConversationStatus(status)
                if status in {"continue", "complete"}
                else ConversationStatus.SAFEGUARD_COMPLETE
            ),
            **common,
        )
    if request.kind is AgentCallKind.JUDGE:
        return JudgeOutput(str(data.get("answer", "")), str(data.get("explanation", "")), **common)
    if request.kind is AgentCallKind.TIE:
        tied = tuple(map(str, request.context.get("tied_answers", ())))
        answer = str(data.get("answer", ""))
        return TieOutput(answer, str(data.get("explanation", "")), tied, **common)
    return AgentOutput(str(data.get("answer", "")), str(data.get("explanation", "")), **common)


class OpenJiuwenAgent:
    """Thin production adapter around ``openjiuwen.core.foundation.llm.Model``."""

    def __init__(
        self,
        model: Model,
        *,
        timeout: float = 60.0,
        parse_retries: int = 1,
        supports_seed: bool = False,
        resources: ResourceController | None = None,
        instructions: str = "",
        role_output: Mapping[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.parse_retries = max(0, parse_retries)
        self.supports_seed = supports_seed
        self.resources = resources
        self.instructions = instructions
        frozen_role_output = getattr(getattr(model, "model_client_config", None), "openrouter_role_output", None)
        if role_output is not None and isinstance(model, Model) and dict(role_output) != frozen_role_output:
            raise ValueError("adapter role output mode differs from the frozen public Model configuration")
        self.role_output = copy.deepcopy(role_output if role_output is not None else frozen_role_output)
        response_format_for("role", self.role_output)
        if isinstance(model, Model) and resources is None:
            raise ValueError("production models require a configured resource controller")
        self._parser = JsonOutputParser()

    def response_format(self, kind: str) -> dict[str, Any] | None:
        return response_format_for(kind, self.role_output)

    @property
    def dispatch_stop_reason(self) -> dict[str, Any] | None:
        return self.resources.ledger.dispatch_stop_reason if self.resources is not None else None

    def begin_logical_request(self, request: GenerationRequest) -> int | None:
        from ..infrastructure.shutdown import raise_if_stopping

        raise_if_stopping()
        if self.resources is None:
            return None
        self.resources.assert_policy_config()
        return self.resources.ledger.begin_logical_request(
            self.resources.backend_identity(self.model),
            {"kind": request.kind, "agent_id": request.agent_id, "logical_call": request.context.get("logical_call")},
        )

    def finish_logical_request(self, token: int | None) -> None:
        if self.resources is not None:
            self.resources.ledger.finish_logical_request(token)

    async def flush_accounting(self) -> None:
        if self.resources is not None:
            await self.resources.ledger.flush_durable()

    def _generation_settings(
        self, request: GenerationRequest, attempt: int
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Forward explicitly supported seeds without mutating shared model configuration."""

        seed = request.context.get("generation_seed")
        if seed is None:
            return {}, None
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("generation_seed must be a non-negative integer")
        if attempt > 1:
            # Runtime repairs are separate requests. This domain is only for
            # standalone adapter repairs outside the runtime's shared budget.
            from ..infrastructure.randomness import derive_seed

            seed = derive_seed(seed, "adapter-repair", attempt - 1)
        descriptor = {
            "generation_seed": seed,
            "seed_forwarded": self.supports_seed,
            "supports_seed": self.supports_seed,
            "logical_call": request.context.get("logical_call"),
            "adapter_attempt": attempt,
        }
        if not self.supports_seed:
            descriptor["limitation"] = "backend does not declare replayable seeded generation"
        return ({"seed": seed} if self.supports_seed else {}), descriptor

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        inherited = "provider_logical_request_id" in request.context
        token = request.context.get("provider_logical_request_id") if inherited else self.begin_logical_request(request)
        if not inherited:
            request = replace(request, context={**request.context, "provider_logical_request_id": token})
        try:
            return await self._generate(request)
        finally:
            if not inherited:
                self.finish_logical_request(token)
                await self.flush_accounting()

    async def _generate(self, request: GenerationRequest) -> GenerationResult:
        errors: list[str] = []
        started = time.perf_counter()
        deadline = min(
            time.monotonic() + self.timeout,
            request.context.get("logical_deadline_monotonic", float("inf")),
            request.context.get("session_deadline_monotonic", float("inf")),
        )
        last_content = ""
        last_usage: dict[str, Any] | None = None
        attempt_usage: list[dict[str, Any] | None] = []
        attempt_records: list[ValidationAttempt] = []
        repair_generations = (
            self.parse_retries if request.max_repair_generations is None else request.max_repair_generations
        )
        base_prompt = request.context.get("repair_base_prompt", request.prompt)
        repair_enabled = repair_generations > 0 or bool(request.context.get("repair_enabled", False))
        if "repair_base_prompt" in request.context and request.prompt not in repair_variants(
            base_prompt, kind=request.kind
        ):
            raise ValueError("a repair request must preserve its complete original input and fixed diagnostic")
        protocol = request.context.get("protocol_instructions") or protocol_instructions(
            request.context.get("prompt_component", request.kind), request.context
        )
        instructions = str(request.context.get("agent_instructions", self.instructions))
        admission: dict[str, Any] = {
            "repair_enabled": repair_enabled,
            "repair_diagnostic_id": REPAIR_DIAGNOSTIC_ID,
        }
        schema = self.response_format(request.kind)
        if request.kind == "role":
            admission.update(
                role_output_protocol=ROLE_OUTPUT_PROTOCOL,
                role_output_mode="json_schema" if schema is not None else "prompt_validator",
                schema_counting=SCHEMA_COUNTING_ID if schema is not None else None,
                response_format=schema,
            )
        if self.resources is not None:
            variants = repair_variants(base_prompt, kind=request.kind) if repair_enabled else (request.prompt,)
            maximum, output_cap = self.resources.preflight_envelope(
                tuple(compose_backend_input(instructions, protocol, body) for body in variants),
                request.kind,
                schema=schema,
            )
            admission.update(maximum_input_tokens=maximum, output_cap=output_cap)
        diagnostic = ""
        for attempt in range(1, repair_generations + 2):
            generation_settings, generation_descriptor = self._generation_settings(request, attempt)
            if request.kind == "role" and isinstance(self.model, Model):
                generation_settings["workflow_call_kind"] = "role"
            if schema is not None:
                generation_settings["response_format"] = copy.deepcopy(schema)
            prompt = request.prompt if attempt == 1 else render_repair(base_prompt, diagnostic)
            messages = compose_backend_input(instructions, protocol, prompt)
            attempt_started = time.perf_counter()
            try:
                physical: list[dict[str, Any]] = []
                if self.resources is not None:
                    bounded = await self.resources.invoke(
                        self.model,
                        messages=messages,
                        kind=request.kind,
                        deadline=deadline,
                        settings=generation_settings,
                        output_parser=self._parser,
                        logical_request_id=request.context.get("provider_logical_request_id"),
                    )
                    response = bounded.response
                    physical = [dict(item) for item in bounded.attempts]
                    last_usage = dict(bounded.metadata)
                else:
                    from ..infrastructure.failure_policy import deadline_scope

                    kind = (
                        "session-deadline" if deadline == request.context.get("session_deadline_monotonic")
                        else "call-deadline"
                    )
                    async with deadline_scope(kind, deadline=deadline):
                        response = await self.model.invoke(
                            messages=list(messages),
                            output_parser=self._parser,
                            timeout=self.timeout,
                            **generation_settings,
                        )
                    last_usage = usage_to_dict(getattr(response, "usage_metadata", None))
                    physical = [last_usage or {}]
            except ResourceFailure as exc:
                # Runtime applies the output-specific transition, retaining the
                # physical ledger and this exception's known/unknown usage.
                current_usage = exc.usage_record
                exc.usage_record = replace(
                    current_usage + sum((_attempt_usage(value, 0.0) for value in attempt_usage), UsageRecord()),
                    raw_metadata={
                        "prior_attempts": tuple(attempt_usage),
                        "interrupted_attempt": current_usage.raw_metadata,
                    },
                )
                raise
            except Exception as exc:  # provider-specific errors remain opaque here
                failure = f"{type(exc).__name__}: {exc}"
                failed_metadata = {"generation": generation_descriptor} if generation_descriptor is not None else None
                attempt_usage.append(failed_metadata)
                attempt_records.append(
                    ValidationAttempt(
                        attempt,
                        "",
                        None,
                        (failure,),
                        _attempt_usage(failed_metadata, time.perf_counter() - attempt_started),
                    )
                )
                return GenerationResult(
                    content=last_content,
                    data=None,
                    usage_metadata=last_usage,
                    wall_seconds=time.perf_counter() - started,
                    attempts=attempt,
                    attempt_usage_metadata=attempt_usage,
                    error=failure,
                    parse_errors=errors,
                    attempt_records=attempt_records,
                )
            last_content = str(getattr(response, "content", "") or "")
            last_usage = {**(last_usage or {}), "effective_input": messages, "admission": admission}
            if generation_descriptor is not None:
                last_usage = {**(last_usage or {}), "generation": generation_descriptor}
            physical[-1] = last_usage
            attempt_usage.extend(physical)
            parser_content = getattr(response, "parser_content", None)
            data, error = validate_payload(
                request.kind,
                parser_content if parser_content is not None else last_content,
            )
            record_data = data
            context_invalid = False
            finish_reason = (last_usage or {}).get("finish_reason") or getattr(response, "finish_reason", None)
            if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
                data, error = None, "output-token-limit: truncated generation is not a valid structured output"
            elif data is not None:
                data, context_error = _validate_answer_context(request, data)
                if context_error is not None:
                    error, context_invalid = context_error, True
            diagnostic = select_repair_diagnostic(error, context_invalid=context_invalid, kind=request.kind)
            attempt_records.append(
                ValidationAttempt(
                    attempt,
                    last_content,
                    record_data,
                    (error,) if error else (),
                    replace(
                        sum(
                            (
                                _attempt_usage(value, (time.perf_counter() - attempt_started) / len(physical))
                                for value in physical
                            ),
                            UsageRecord(),
                        ),
                        raw_metadata={**last_usage, "physical_attempts": physical},
                    ),
                )
            )
            if data is not None:
                return GenerationResult(
                    content=last_content,
                    data=data,
                    usage_metadata=last_usage,
                    wall_seconds=time.perf_counter() - started,
                    attempts=attempt,
                    attempt_usage_metadata=attempt_usage,
                    parse_errors=errors,
                    attempt_records=attempt_records,
                )
            errors.append(error or "invalid structured response")
        return GenerationResult(
            content=last_content,
            data=None,
            usage_metadata=last_usage,
            wall_seconds=time.perf_counter() - started,
            attempts=repair_generations + 1,
            attempt_usage_metadata=attempt_usage,
            error=errors[-1] if errors else "invalid structured response",
            parse_errors=errors,
            attempt_records=attempt_records,
            validation_exhausted=True,
        )

    async def invoke(self, request: AgentRequest):
        result = await self.generate(
            GenerationRequest(
                kind=request.kind.value,
                prompt=request.prompt,
                agent_id=request.agent_id,
                context={**request.context, "prompt_component": request.prompt_component},
                max_repair_generations=request.max_repair_generations,
            )
        )
        return parsed_output(request, result)


class DeterministicFakeAgent:
    """Offline scripted agent; repeated keys are consumed in FIFO order."""

    def __init__(
        self,
        agent_id: int,
        scripts: Mapping[str, Sequence[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
        *,
        default_answer: str | None = None,
        latency: float = 0.0,
    ) -> None:
        self.agent_id = agent_id
        self.default_answer = default_answer or f"answer-{agent_id}"
        self.latency = latency
        self.calls: list[GenerationRequest] = []
        self.active = 0
        self.max_active = 0
        self._scripts: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for key, values in (scripts or {}).items():
            if isinstance(values, Mapping):
                values = [values]
            self._scripts[key].extend(dict(value) for value in values)

    def _next(self, request: GenerationRequest) -> dict[str, Any]:
        tag = str(request.context.get("tag", ""))
        round_index = request.context.get("round")
        keys = (
            f"{request.kind}:{tag}:{round_index}",
            f"{request.kind}:{tag}",
            request.kind,
        )
        for key in keys:
            if self._scripts[key]:
                return self._scripts[key].popleft()
        if request.kind == "role":
            return {"status": "complete", "feedback": "No further change is needed."}
        return {"answer": self.default_answer, "explanation": "deterministic fake output"}

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        started = time.perf_counter()
        try:
            if self.latency:
                await asyncio.sleep(self.latency)
            data = self._next(request)
            content = json.dumps(data, sort_keys=True)
            validated, error = validate_payload(request.kind, data)
            metadata = {
                "input_tokens": len(request.prompt.split()),
                "output_tokens": len(content.split()),
                "total_tokens": len(request.prompt.split()) + len(content.split()),
                "synthetic": True,
                "total_cost": 0.0,
                "reference_cost": 0.0,
            }
            return GenerationResult(
                content=content,
                data=validated,
                usage_metadata=metadata,
                wall_seconds=time.perf_counter() - started,
                error=error,
                parse_errors=[error] if error else [],
                attempt_records=[
                    ValidationAttempt(
                        1,
                        content,
                        validated,
                        (error,) if error else (),
                        _attempt_usage(metadata, time.perf_counter() - started),
                    )
                ],
                validation_exhausted=error is not None,
            )
        finally:
            self.active -= 1

    async def invoke(self, request: AgentRequest):
        result = await self.generate(
            GenerationRequest(
                kind=request.kind.value,
                prompt=request.prompt,
                agent_id=request.agent_id,
                context=dict(request.context),
                max_repair_generations=request.max_repair_generations,
            )
        )
        return parsed_output(request, result)
