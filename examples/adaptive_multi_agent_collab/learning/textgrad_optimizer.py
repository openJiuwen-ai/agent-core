"""Real TextGrad proposal generation through the public openJiuwen model API.

TextGrad owns the local textual graph and one optimizer step. The surrounding
trainer owns structural validation, workflow evaluation, acceptance, and resume.
Importing this module neither imports TextGrad nor creates a model client.
"""

from __future__ import annotations

from ..paths import PACKAGE_ROOT
import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import logging
import math
import os
import random
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..providers.adapter import usage_to_dict
from ..providers.model_io import _truncated
from ..workflows.prompts import (
    OptimizationAttempt,
    PromptOccurrence,
    PromptProposal,
    PromptSpec,
    TextualGradient,
)
from ..infrastructure.randomness import derive_seed
from ..schemas import UsageRecord
from ..providers.resources import AccountingFailure, BudgetExhausted, PolicyStopped, ResourceController, ResourceFailure

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm import Model


EVALUATOR_INSTRUCTION_ID = "component_failure_evaluation_v1"
OPTIMIZER_INSTRUCTION_ID = "reusable_component_revision_v1"
TEXTGRAD_PROTOCOL_ID = "bounded_feedback_reference_paid_engine_binding_v3"
TEXTGRAD_REPAIR_PROTOCOL_ID = "bounded_textgrad_output_repair_v1"
TEXTGRAD_REPAIR_DIAGNOSTICS = {
    "evaluation": {
        "empty": "Return fresh complete, nonempty textual evaluation feedback for the original task.",
        "truncated": "Return fresh shorter complete textual evaluation feedback; do not continue the previous output.",
    },
    "backward": {
        "empty": "Return fresh complete, nonempty textual gradient feedback for the original task.",
        "truncated": "Return fresh shorter complete textual gradient feedback; do not continue the previous output.",
    },
    "proposal": {
        "empty": "Return one fresh complete template between <IMPROVED_VARIABLE> and </IMPROVED_VARIABLE>, preserving all structural constraints.",
        "truncated": "Return one fresh shorter complete template between <IMPROVED_VARIABLE> and </IMPROVED_VARIABLE>, preserving all structural constraints; do not continue prior output.",
        "delimiters": "Return one complete template between exactly one <IMPROVED_VARIABLE> and </IMPROVED_VARIABLE> pair, preserving all structural constraints.",
        "structure": "Return one fresh complete template between <IMPROVED_VARIABLE> and </IMPROVED_VARIABLE>. Preserve the immutable scaffold, required placeholders and markers exactly; edit only permitted guidance.",
    },
}


def textgrad_repair_messages(messages: Sequence[Mapping[str, str]], stage: str, reason: str) -> list[dict[str, str]]:
    """Original complete chat plus one fixed diagnostic; rejected output never enters it."""
    return [dict(item) for item in messages] + [
        {"role": "user", "content": TEXTGRAD_REPAIR_DIAGNOSTICS[stage][reason]}
    ]


def textgrad_output_failure(content: str, metadata: Mapping[str, Any], stage: str, spec: PromptSpec) -> str | None:
    """Validate before the library consumes text or mutates its working variable."""
    if any(_truncated(metadata.get(key))
           for key in ("finish_reason", "native_finish_reason")):
        return "truncated"
    if not content.strip():
        return "empty"
    if stage == "proposal":
        start, end = "<IMPROVED_VARIABLE>", "</IMPROVED_VARIABLE>"
        if content.count(start) != 1 or content.count(end) != 1 or content.index(start) >= content.index(end):
            return "delimiters"
        template = content.split(start, 1)[1].split(end, 1)[0].strip()
        if not template or replace(spec, template=template).validate():
            return "structure"
    return None

EVALUATOR_INSTRUCTION = (
    "Evaluate the selected reusable prompt template using the separately labelled "
    "fixed training record. Diagnose defects local to this component and explain "
    "what reusable instruction could improve its intended behavior. Distinguish "
    "these defects from routing errors, another prompt component, insufficient "
    "submitted information, or stochastic generation. The downstream failure is "
    "a selection heuristic, not proof of causation. Preserve the structural "
    "constraints. Never copy a training query, answer, identifier, or trace fragment "
    "into a reusable template. Give textual feedback, not a replacement template."
)
OPTIMIZER_INSTRUCTION = (
    "You improve one reusable collaboration prompt template from its attached "
    "textual feedback and context. Propose exactly one revision satisfying every "
    "structural constraint. Do not copy a training query, answer, identifier, or "
    "trace fragment into the template. Keep every mandatory named placeholder and "
    "output marker unchanged. Return the entire revised template between "
    "{new_variable_start_tag} and {new_variable_end_tag}."
)
_IMPORT_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class TextGradConfig:
    """Pinned proposal engines and protocol; no memory or momentum variant."""

    backward_engine: str
    backward_revision: str
    optimizer_engine: str
    optimizer_revision: str
    package_version: str = "0.1.8"
    backward_decoding: tuple[tuple[str, Any], ...] = ()
    optimizer_decoding: tuple[tuple[str, Any], ...] = ()
    backward_supports_seed: bool = False
    optimizer_supports_seed: bool = False
    run_seed: int = 42
    call_timeout_seconds: float = 120.0
    proposal_timeout_seconds: float = 600.0
    gradient_memory: int = 0
    momentum: int = 0
    cross_tokenizer_expansion: float | None = None
    output_repair_limit: int = 0

    def __post_init__(self) -> None:
        for name in (
            "backward_engine",
            "backward_revision",
            "optimizer_engine",
            "optimizer_revision",
            "package_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be recorded")
        if self.gradient_memory != 0 or self.momentum != 0:
            raise ValueError("the primary TextGrad protocol requires zero memory and no momentum")
        if type(self.output_repair_limit) is not int or self.output_repair_limit not in (0, 1):
            raise ValueError("TextGrad output repair must be disabled or bounded to one extra generation")
        if self.cross_tokenizer_expansion is not None and (
            not math.isfinite(self.cross_tokenizer_expansion) or self.cross_tokenizer_expansion < 1
        ):
            raise ValueError("cross-tokenizer output reservation expansion must be finite and at least one")
        if self.run_seed < 0:
            raise ValueError("run_seed must be non-negative")
        if self.call_timeout_seconds <= 0 or self.proposal_timeout_seconds <= 0:
            raise ValueError("TextGrad operational timeouts must be positive")
        forbidden = {"messages", "output_parser", "timeout", "seed", "model", "max_tokens"}
        for name in ("backward_decoding", "optimizer_decoding"):
            settings = tuple(getattr(self, name))
            if len(dict(settings)) != len(settings):
                raise ValueError(f"{name} keys must be unique")
            if forbidden.intersection(dict(settings)):
                raise ValueError(f"{name} cannot override model identity or controlled call arguments")
            json.dumps(dict(settings), allow_nan=False)
            object.__setattr__(self, name, settings)


def _load_textgrad(expected_version: str) -> tuple[Any, Any]:
    """Require the real pinned package, suppressing its import-time file logging."""

    try:
        installed = importlib.metadata.version("textgrad")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ImportError(
            "TextGrad is required for prompt optimization and is not installed. "
            "Dependency installation requires explicit approval; no fallback is used."
        ) from exc
    if installed != expected_version:
        raise RuntimeError(f"TextGrad version mismatch: configured {expected_version}, installed {installed}")
    with (
        _IMPORT_LOCK,
        tempfile.TemporaryDirectory(prefix=".textgrad-import-", dir=PACKAGE_ROOT) as log_directory,
    ):
        # PyPI 0.1.8 unconditionally opens a log file; upstream main adds a
        # disable flag without changing its version. Handle both versions by
        # containing that import-only file and removing its own handler.
        settings = {
            "TEXTGRAD_DISABLE_LOGGING": "true",
            "TEXTGRAD_LOG_DIR": log_directory,
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }
        previous = {name: os.environ.get(name) for name in settings}
        os.environ.update(settings)
        try:
            tg = importlib.import_module("textgrad")
            loss_module = importlib.import_module("textgrad.loss")
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            logger = logging.getLogger("textgrad")
            for handler in tuple(logger.handlers):
                if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).is_relative_to(
                    log_directory
                ):
                    logger.removeHandler(handler)
                    handler.close()
    if any(isinstance(handler, logging.FileHandler) for handler in tg.logger.handlers):
        raise RuntimeError(
            "TextGrad was already imported with an external file logger. Initialize "
            "the proposer before importing TextGrad so overhead stays in its audit trail."
        )
    tg.logger.propagate = False
    if tg.singleton_backward_engine.get_engine() is not None:
        raise RuntimeError(
            "TextGrad has a process-global backward engine. This isolated proposer "
            "requires explicit engines and will not replace the existing engine."
        )
    return tg, loss_module.MultiFieldEvaluation


def textgrad_metadata(config: TextGradConfig, resource_profiles: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Pure engine fingerprint shared by offline preflight and the live proposer."""

    configuration = asdict(config)
    # Absent repair remains the exact historical metadata contract. The
    # prospective amendment explicitly opts into the new protocol.
    if config.output_repair_limit == 0:
        configuration.pop("output_repair_limit")
    return {
        "backend": "textgrad",
        "protocol": TEXTGRAD_REPAIR_PROTOCOL_ID if config.output_repair_limit else TEXTGRAD_PROTOCOL_ID,
        **configuration,
        **({"output_repair_diagnostics": TEXTGRAD_REPAIR_DIAGNOSTICS} if config.output_repair_limit else {}),
        "evaluator_instruction_version": EVALUATOR_INSTRUCTION_ID,
        "optimizer_instruction_version": OPTIMIZER_INSTRUCTION_ID,
        "evaluator_instruction_sha256": hashlib.sha256(EVALUATOR_INSTRUCTION.encode()).hexdigest(),
        "optimizer_instruction_sha256": hashlib.sha256(OPTIMIZER_INSTRUCTION.encode()).hexdigest(),
        "resource_profiles": {role: dict(profile) for role, profile in resource_profiles.items()},
    }


class TextGradPromptProposer:
    """Construct, backpropagate, and discard one genuine TextGrad graph per call."""

    def __init__(
        self,
        backward_model: Model,
        optimizer_model: Model,
        *,
        config: TextGradConfig,
        resources: Mapping[str, ResourceController] | None = None,
    ) -> None:
        self.backward_model = backward_model
        self.optimizer_model = optimizer_model
        self.config = config
        self.resources = dict(resources or {})
        if self.resources:
            if set(self.resources) != {"backward", "optimizer"} or any(
                item is None for item in self.resources.values()
            ):
                raise ValueError("TextGrad requires both engine resource controllers or explicit offline engines")
            first, second = (self.resources[role].profile for role in ("backward", "optimizer"))
            identity = lambda profile: (profile.tokenizer_kind, profile.tokenizer_name, profile.tokenizer_revision)
            if identity(first) != identity(second) and config.cross_tokenizer_expansion is None:
                raise ValueError("different TextGrad tokenizers require a declared cross_tokenizer_expansion bound")
        from openjiuwen.core.foundation.llm import Model

        if any(isinstance(model, Model) for model in (backward_model, optimizer_model)) and set(self.resources) != {
            "backward",
            "optimizer",
        }:
            raise ValueError("production TextGrad requires both engine resource controllers")

    def metadata(self) -> dict[str, Any]:
        return textgrad_metadata(
            self.config, {role: asdict(controller.profile) for role, controller in self.resources.items()}
        )

    async def propose(
        self,
        spec: PromptSpec,
        evidence: Sequence[PromptOccurrence],
        *,
        iteration_id: str,
    ) -> PromptProposal:
        """Return uncommitted text and all overhead attempts, including failures."""

        return await self._propose(spec, evidence, iteration_id=iteration_id, evidence_partition="train")

    async def propose_compatibility(
        self, spec: PromptSpec, evidence: Sequence[PromptOccurrence], *, iteration_id: str
    ) -> PromptProposal:
        """Exercise the same real graph using explicitly synthetic, disposable evidence."""

        return await self._propose(spec, evidence, iteration_id=iteration_id, evidence_partition="synthetic")

    async def _propose(
        self, spec: PromptSpec, evidence: Sequence[PromptOccurrence], *, iteration_id: str, evidence_partition: str
    ) -> PromptProposal:

        failed = tuple(evidence)
        if not failed:
            return PromptProposal(None, error="no failed training occurrences", metadata=self.metadata())
        if any(item.component != spec.key or not item.failed or item.split != evidence_partition for item in failed):
            label = "training" if evidence_partition == "train" else evidence_partition
            raise ValueError(f"TextGrad accepts only failed {label} occurrences of the selected component")
        if not iteration_id:
            raise ValueError("iteration_id must identify the attempted prompt iteration")
        if spec.validate():
            raise ValueError("the accepted prompt template is structurally invalid")
        # Dependency/configuration failures are explicit, before any engine call.
        tg, evaluator_type = _load_textgrad(self.config.package_version)
        loop = asyncio.get_running_loop()
        attempts: list[OptimizationAttempt] = []
        cancelled = threading.Event()
        pending: set[Any] = set()
        pending_lock = threading.Lock()
        config = self.config
        models = {"backward": self.backward_model, "optimizer": self.optimizer_model}
        controllers = self.resources
        selection_metadata: dict[str, Any] = {}
        overall_deadline = time.monotonic() + config.proposal_timeout_seconds
        logical_calls = 0
        stopped_errors: list[BaseException] = []
        invoke_tasks: set[asyncio.Task[Any]] = set()

        async def drain_graph() -> None:
            # concurrent.futures cancellation releases the graph thread before
            # its coroutine has necessarily finished resource/accounting cleanup.
            # A proposal audit must not snapshot that mutable attempt list early.
            await asyncio.gather(task, return_exceptions=True)
            if invoke_tasks:
                from ..infrastructure.shutdown import CLEANUP_SECONDS, bounded_cleanup, current_shutdown
                _, pending_count = await bounded_cleanup(list(invoke_tasks), timeout=CLEANUP_SECONDS)
                if pending_count:
                    stop = AccountingFailure("TextGrad cleanup deadline; physical operations remain unconfirmed")
                    stopped_errors.append(stop)
                    state = current_shutdown()
                    if state is not None:
                        state.request(str(stop))

        class Engine(tg.EngineLM):  # type: ignore[name-defined]  # Pinned dependency is loaded lazily.
            def __init__(self, role: str) -> None:
                self.role = role
                self.stage = "evaluation" if role == "backward" else "proposal"
                self.model_string = getattr(config, f"{role}_engine")

            def generate(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> str:
                if not isinstance(prompt, str):
                    raise TypeError("the component optimization protocol accepts text-only prompts")
                if kwargs:
                    raise ValueError("TextGrad engine cannot change the recorded decoding configuration")
                with pending_lock:
                    if cancelled.is_set():
                        raise TimeoutError("TextGrad proposal was cancelled or timed out")
                    future = asyncio.run_coroutine_threadsafe(self._invoke(prompt, system_prompt), loop)
                    pending.add(future)
                try:
                    return future.result(timeout=config.call_timeout_seconds + 1.0)
                finally:
                    if not future.done():
                        future.cancel()
                    with pending_lock:
                        pending.discard(future)

            def __call__(self, *args: Any, **kwargs: Any) -> str:
                return self.generate(*args, **kwargs)

            async def _invoke(self, prompt: str, system_prompt: str | None) -> str:
                current = asyncio.current_task()
                assert current is not None
                invoke_tasks.add(current)
                try:
                    return await self._invoke_body(prompt, system_prompt)
                finally:
                    invoke_tasks.discard(current)

            async def _invoke_body(self, prompt: str, system_prompt: str | None) -> str:
                nonlocal logical_calls
                logical_calls += 1
                logical_index = logical_calls
                logical_id = f"{iteration_id}:{spec.key}:{self.stage}:{logical_index}"
                receipt_prefix = f"textgrad:{self.stage}:{hashlib.sha256(logical_id.encode()).hexdigest()}:output:"
                seed = derive_seed(config.run_seed, "textgrad", iteration_id, spec.key, self.stage, logical_index)
                supports_seed = getattr(config, f"{self.role}_supports_seed")
                settings = dict(getattr(config, f"{self.role}_decoding"))
                if supports_seed:
                    settings["seed"] = seed
                original = [{"role": "user", "content": prompt}]
                if system_prompt is not None:
                    original.insert(0, {"role": "system", "content": system_prompt})
                deadline = min(overall_deadline, time.monotonic() + config.call_timeout_seconds)
                controller = controllers.get(self.role)
                remaining_transport_retries = controller.limits.transport_retries if controller is not None else 0
                logical_token = None
                if controller is not None:
                    if config.output_repair_limit and any(
                        str(entry.get("kind", "")).startswith(receipt_prefix) for entry in controller.ledger.entries
                    ):
                        raise AccountingFailure("TextGrad logical call already has durable attempts; replay is forbidden")
                    variants = [original]
                    if config.output_repair_limit:
                        variants.extend(textgrad_repair_messages(original, self.stage, reason)
                                        for reason in TEXTGRAD_REPAIR_DIAGNOSTICS[self.stage])
                    controller.preflight_envelope(variants, f"textgrad:{self.stage}")
                    logical_token = controller.ledger.begin_logical_request(
                        controller.backend_identity(models[self.role]),
                        {"kind": f"textgrad:{self.stage}", "logical_call_id": logical_id,
                         "output_repair_limit": config.output_repair_limit},
                    )
                reason = None
                try:
                    for output_attempt in range(config.output_repair_limit + 1):
                        messages = (original if reason is None
                                    else textgrad_repair_messages(original, self.stage, reason))
                        index = len(attempts) + 1
                        started = time.perf_counter()
                        content = ""
                        metadata: Mapping[str, Any] = {}
                        error = None
                        physical_usage: UsageRecord | None = None
                        physical_records: Sequence[Mapping[str, Any]] = ()
                        dispatched_without_controller = False
                        failure = None
                        try:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("TextGrad logical-call deadline before output generation")
                            if controller is not None:
                                active_controller = controller
                                if remaining_transport_retries != controller.limits.transport_retries:
                                    # Output repair shares the logical call's safe
                                    # transport allowance; never reset it per output.
                                    active_controller = ResourceController(
                                        controller.profile, controller.counter, controller.ledger,
                                        limits=replace(controller.limits, transport_retries=remaining_transport_retries),
                                        failure_policy=controller.expected_failure_policy,
                                    )
                                bounded = await active_controller.invoke(
                                    models[self.role], messages=messages,
                                    kind=(receipt_prefix + str(output_attempt) if config.output_repair_limit
                                          else f"textgrad:{self.stage}"),
                                    deadline=deadline, settings={"model": self.model_string, **settings},
                                    logical_request_id=logical_token,
                                )
                                response = bounded.response
                                metadata = bounded.metadata
                                physical_records = bounded.attempts
                                from ..providers.adapter import _attempt_usage
                                physical_usage = sum(
                                    (_attempt_usage(dict(item), 0.0) for item in bounded.attempts), UsageRecord()
                                )
                                remaining_transport_retries -= max(0, len(bounded.attempts) - 1)
                            else:
                                # Explicit injected offline models still share the original
                                # logical-call deadline across both output attempts.
                                async with asyncio.timeout_at(deadline):
                                    dispatched_without_controller = True
                                    response = await models[self.role].invoke(
                                        messages=messages, model=self.model_string,
                                        timeout=config.call_timeout_seconds, **settings,
                                    )
                                metadata = usage_to_dict(getattr(response, "usage_metadata", None)) or {}
                            content = str(getattr(response, "content", "") or "")
                            failure = textgrad_output_failure(content, metadata, self.stage, spec)
                            # Historical graphs retain their old structural validation
                            # boundary; the amended graph validates before TGD mutation.
                            if not config.output_repair_limit and failure in {"delimiters", "structure"}:
                                failure = None
                            if failure is not None:
                                error = f"TextGrad output invalid: {failure}"
                            else:
                                return content
                        except (Exception, asyncio.CancelledError) as exc:
                            if isinstance(exc, ResourceFailure):
                                physical_usage = getattr(exc, "usage_record")
                            elif getattr(exc, "usage_record", None) is not None:
                                physical_usage = getattr(exc, "usage_record")
                            error = f"{type(exc).__name__}: {exc}"
                            if physical_usage is not None and not physical_records:
                                physical_records = physical_usage.raw_metadata.get("physical_attempts", ())
                            if isinstance(exc, (AccountingFailure, BudgetExhausted, asyncio.CancelledError)):
                                stopped_errors.append(exc)
                            # Only output defects above can reach the repair loop.
                            # Provider/accounting/cancellation failures never do.
                            raise
                        finally:
                            usage = UsageRecord(
                                input_tokens=(physical_usage.input_tokens if physical_usage is not None
                                              else metadata.get("input_tokens")),
                                output_tokens=(physical_usage.output_tokens if physical_usage is not None
                                               else metadata.get("output_tokens")),
                                model_calls=(physical_usage.model_calls if physical_usage is not None
                                             else int(dispatched_without_controller)),
                                monetary_cost=(physical_usage.monetary_cost if physical_usage is not None
                                               else metadata.get("reference_cost")),
                                paid_monetary_cost=(physical_usage.paid_monetary_cost if physical_usage is not None
                                                    else metadata.get("total_cost")),
                                wall_seconds=time.perf_counter() - started, model_name=self.model_string,
                                raw_metadata={
                                    **metadata, "system_prompt": system_prompt,
                                    "seed_forwarded": supports_seed,
                                    "decoding_configuration": dict(getattr(config, f"{self.role}_decoding")),
                                    "logical_call_id": logical_id, "output_attempt": output_attempt,
                                    "repair_reason": reason, "output_failure": failure,
                                    "repair_diagnostic_id": (None if reason is None else
                                        f"{TEXTGRAD_REPAIR_PROTOCOL_ID}:{self.stage}:{reason}"),
                                    "effective_messages": messages,
                                    "physical_attempts": tuple(dict(item) for item in physical_records),
                                },
                            )
                            attempts.append(OptimizationAttempt(
                                self.stage, index, self.model_string, prompt, content, usage,
                                generation_seed=seed, supports_seed=supports_seed, error=error,
                            ))
                        if output_attempt == config.output_repair_limit:
                            raise ValueError(f"TextGrad output repair exhausted: {failure}")
                        reason = failure
                    raise AssertionError("TextGrad output loop did not terminate")
                finally:
                    if controller is not None:
                        controller.ledger.finish_logical_request(logical_token)
                        await controller.ledger.flush_durable()

        def work(records: Sequence[PromptOccurrence], *, planning: bool = False) -> tuple[str, TextualGradient]:
            # A dry graph uses the pinned library's exact input construction and
            # symbolic output reservations. It never proposes a revision or
            # invokes TGD.step; the actual graph below does each exactly once.
            symbols: list[tuple[str, str]] = []

            def check_plan(prompt: str, system_prompt: str | None, role: str, stage: str) -> None:
                controller = controllers[role]
                messages = [{"role": "system", "content": system_prompt or ""}, {"role": "user", "content": prompt}]
                variants = [messages]
                if config.output_repair_limit:
                    variants.extend(textgrad_repair_messages(messages, stage, reason)
                                    for reason in TEXTGRAD_REPAIR_DIAGNOSTICS[stage])
                count = max(controller.counter.count_input(item) for item in variants)
                # Preserve full known contexts and reserve each unknown model
                # output at its declared maximum, plus token-boundary framing.
                reserved_count = count
                for marker, source_role in symbols:
                    source = controllers[source_role]
                    maximum = (
                        source.profile.reasoning_allowance
                        if source.profile.reasoning_in_output_budget
                        else source.limits.textgrad_output
                    )
                    source_identity = (
                        source.profile.tokenizer_kind,
                        source.profile.tokenizer_name,
                        source.profile.tokenizer_revision,
                    )
                    target_identity = (
                        controller.profile.tokenizer_kind,
                        controller.profile.tokenizer_name,
                        controller.profile.tokenizer_revision,
                    )
                    expansion = (
                        1.0 if source_identity == target_identity else float(config.cross_tokenizer_expansion or 1.0)
                    )
                    copies = prompt.count(marker) + (system_prompt or "").count(marker)
                    reserved_count += copies * (math.ceil(int(maximum or 0) * expansion) + 32)
                input_cap, output_cap = controller.limits.caps("textgrad")
                if controller.profile.reasoning_in_output_budget:
                    output_cap = int(controller.profile.reasoning_allowance or 0)
                if (
                    reserved_count > input_cap
                    or reserved_count + output_cap + controller.limits.context_reserve
                    > controller.profile.context_limit
                ):
                    raise ResourceFailure("TextGrad complete-record input reservation exceeds budget")

            class PlanningEngine(tg.EngineLM):  # type: ignore[name-defined]
                def __init__(self, role: str) -> None:
                    self.role = role
                    self.stage = "evaluation"
                    self.model_string = "offline-textgrad-request-plan"

                def generate(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> str:
                    check_plan(prompt, system_prompt, self.role, self.stage)
                    marker = f"__reserved_textgrad_output_{len(symbols)}__"
                    symbols.append((marker, self.role))
                    return marker

                def __call__(self, *args: Any, **kwargs: Any) -> str:
                    return self.generate(*args, **kwargs)

            backward = PlanningEngine("backward") if planning else Engine("backward")
            optimizer_engine = PlanningEngine("optimizer") if planning else Engine("optimizer")
            variable = tg.Variable(spec.template, requires_grad=True, role_description=spec.purpose)
            constraints = spec.structural_constraints()
            fields = (
                "selected reusable template",
                "training query",
                "rendered prompt",
                "complete recorded effective input",
                "generated response",
                "downstream trace",
                "observed outcome",
                "known correct answer",
                "complete return",
                "conversation credits",
                "intended behavior",
                "structural constraints",
            )
            instruction = tg.Variable(
                EVALUATOR_INSTRUCTION,
                requires_grad=False,
                role_description="component-specific failure evaluation instruction",
            )
            evaluator = evaluator_type(instruction, list(fields), engine=backward)
            optimizer = tg.TextualGradientDescent(
                parameters=[variable],
                engine=optimizer_engine,
                constraints=list(constraints),
                optimizer_system_prompt=OPTIMIZER_INSTRUCTION,
                gradient_memory=0,
            )
            try:
                losses = []
                for item in records:
                    values = (
                        item.query,
                        item.rendered_prompt,
                        json.dumps(item.effective_input, ensure_ascii=False, sort_keys=True, default=str),
                        item.response,
                        json.dumps(dict(item.trace), ensure_ascii=False, sort_keys=True, default=str),
                        item.outcome,
                        item.gold_answer,
                        str(item.complete_return),
                        json.dumps(dict(item.conversation_credits), sort_keys=True),
                        spec.purpose,
                        "\n".join(constraints),
                    )
                    fixed = [
                        tg.Variable(value, requires_grad=False, role_description=role)
                        for role, value in zip(fields[1:], values, strict=True)
                    ]
                    losses.append(evaluator([variable, *fixed]))
                    # MultiFieldEvaluation labels inputs by mutating their role
                    # descriptions. Restore the component's semantic role before
                    # backward and optimization, without using prose as a Python
                    # format-field name (punctuation would become attribute access).
                    variable.set_role_description(spec.purpose)
                optimizer.zero_grad()
                backward.stage = "backward"
                total = tg.sum(losses)
                # The pinned library stores predecessors/gradients in sets.
                # Preserve the recorded minibatch order across processes without
                # replacing its aggregation, backward, or mutation operations.
                total.predecessors = tuple(losses)
                total.backward(engine=backward)
                variable.gradients = tuple(
                    sorted(
                        variable.gradients,
                        key=lambda item: (
                            json.dumps(variable.gradients_context[item], sort_keys=True, default=str),
                            item.value,
                        ),
                    )
                )
                gradient = TextualGradient(spec.key, variable.get_gradient_text(), tuple(records))
                if not gradient.critique.strip():
                    raise ValueError("TextGrad backward produced no textual feedback")
                if planning:
                    check_plan(optimizer._update_prompt(variable), optimizer.optimizer_system_prompt, "optimizer", "proposal")
                    return "", gradient
                optimizer.step()
                # step() returns None; the candidate is the working variable's value.
                return str(variable.value), gradient
            finally:
                variable.reset_gradients()
                optimizer.zero_grad()

        ordered = list(enumerate(failed))
        selection_seed = derive_seed(config.run_seed, "textgrad_feedback", iteration_id, spec.key)
        random.Random(selection_seed).shuffle(ordered)
        selected: list[PromptOccurrence] = []
        selected_ids: list[int] = []
        for index, item in ordered:
            if len(selected) >= 2:
                break
            if controllers:
                try:
                    work([*selected, item], planning=True)
                except ResourceFailure:
                    continue
            selected.append(item)
            selected_ids.append(index)
        selection_metadata = {
            "feedback_selection_seed": selection_seed,
            "selected_occurrence_indices": selected_ids,
            "omitted_occurrence_indices": [index for index in range(len(failed)) if index not in selected_ids],
            "occurrence_ids": [f"{item.example_id}:{item.generation_id}:{index}" for index, item in enumerate(failed)],
        }
        if not selected:
            return PromptProposal(
                None,
                error="resource-failed: no complete failure record fits TextGrad input budgets",
                metadata={**self.metadata(), **selection_metadata},
            )
        task = asyncio.create_task(asyncio.to_thread(work, selected))
        try:
            async with asyncio.timeout(config.proposal_timeout_seconds):
                template, gradient = await asyncio.shield(task)
            return PromptProposal(
                template, gradient, tuple(attempts), metadata={**self.metadata(), **selection_metadata}
            )
        except asyncio.CancelledError as exc:
            cancelled.set()
            with pending_lock:
                for future in tuple(pending):
                    future.cancel()
            await drain_graph()
            exc.prompt_proposal = PromptProposal(  # type: ignore[attr-defined]
                None, attempts=tuple(attempts), error=f"CancelledError: {exc}",
                metadata={**self.metadata(), **selection_metadata,
                          "operational_stop": True, "failure_disposition": "cancelled"},
            )
            raise
        except Exception as exc:
            cancelled.set()
            with pending_lock:
                futures = tuple(pending)
                for future in futures:
                    future.cancel()
            # Drain the finite local graph so no late engine calls or audit writes
            # remain after the iteration has been recorded as unsuccessful.
            await drain_graph()
            original = next((item for item in stopped_errors
                             if isinstance(item, (AccountingFailure, BudgetExhausted))), exc)
            if not isinstance(exc, TimeoutError) and original is exc:
                original = next((item for item in stopped_errors if isinstance(item, asyncio.CancelledError)), exc)
            disposition = (
                "cancelled" if isinstance(original, asyncio.CancelledError) else
                "policy-stop" if isinstance(original, PolicyStopped) else
                "accounting-stop" if isinstance(original, AccountingFailure) else
                "budget-exhausted" if isinstance(original, BudgetExhausted) else "proposal-failure"
            )
            proposal = PromptProposal(
                None,
                attempts=tuple(attempts),
                error=f"{type(original).__name__}: {original}",
                metadata={
                    **self.metadata(),
                    **selection_metadata,
                    "budget_exhausted": isinstance(original, BudgetExhausted) and not isinstance(original, PolicyStopped),
                    "operational_stop": isinstance(original, (AccountingFailure, PolicyStopped, asyncio.CancelledError)),
                    "failure_disposition": disposition,
                },
            )
            if isinstance(original, (AccountingFailure, BudgetExhausted, asyncio.CancelledError)):
                setattr(original, "prompt_proposal", proposal)
                raise original
            return proposal
