"""Real TextGrad proposal generation through the public openJiuwen model API.

TextGrad owns the local textual graph and one optimizer step. The surrounding
trainer owns structural validation, workflow evaluation, acceptance, and resume.
Importing this module neither imports TextGrad nor creates a model client.
"""

from __future__ import annotations

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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .adapter import usage_to_dict
from .prompts import (
    OptimizationAttempt,
    PromptOccurrence,
    PromptProposal,
    PromptSpec,
    TextualGradient,
)
from .randomness import derive_seed
from .schemas import UsageRecord
from .resources import BudgetExhausted, ResourceController, ResourceFailure

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm import Model


EVALUATOR_INSTRUCTION_ID = "component_failure_evaluation_v1"
OPTIMIZER_INSTRUCTION_ID = "reusable_component_revision_v1"
TEXTGRAD_PROTOCOL_ID = "bounded_feedback_reference_paid_engine_binding_v3"

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
        tempfile.TemporaryDirectory(prefix=".textgrad-import-", dir=Path(__file__).resolve().parent) as log_directory,
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

    return {
        "backend": "textgrad",
        "protocol": TEXTGRAD_PROTOCOL_ID,
        **asdict(config),
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
                index = len(attempts) + 1
                seed = derive_seed(config.run_seed, "textgrad", iteration_id, spec.key, self.stage, index)
                supports_seed = getattr(config, f"{self.role}_supports_seed")
                settings = dict(getattr(config, f"{self.role}_decoding"))
                if supports_seed:
                    settings["seed"] = seed
                messages = [{"role": "user", "content": prompt}]
                if system_prompt is not None:
                    messages.insert(0, {"role": "system", "content": system_prompt})
                started = time.perf_counter()
                content = ""
                metadata: Mapping[str, Any] = {}
                error = None
                physical_usage: UsageRecord | None = None
                try:
                    if self.role in controllers:
                        bounded = await controllers[self.role].invoke(
                            models[self.role],
                            messages=messages,
                            kind=f"textgrad:{self.stage}",
                            deadline=min(overall_deadline, time.monotonic() + config.call_timeout_seconds),
                            settings={"model": self.model_string, **settings},
                        )
                        response = bounded.response
                        metadata = bounded.metadata
                        from .adapter import _attempt_usage

                        physical_usage = sum(
                            (_attempt_usage(dict(item), 0.0) for item in bounded.attempts), UsageRecord()
                        )
                    else:
                        async with asyncio.timeout(config.call_timeout_seconds):
                            response = await models[self.role].invoke(
                                messages=messages,
                                model=self.model_string,
                                timeout=config.call_timeout_seconds,
                                **settings,
                            )
                        metadata = usage_to_dict(getattr(response, "usage_metadata", None)) or {}
                    content = str(getattr(response, "content", "") or "")
                    if metadata.get("finish_reason") in {"length", "max_tokens", "max_output_tokens"}:
                        raise ResourceFailure("TextGrad output-token-limit: truncated generation", physical_usage)
                    if not content.strip():
                        raise ValueError("TextGrad engine returned empty text")
                    return content
                except (Exception, asyncio.CancelledError) as exc:
                    if isinstance(exc, ResourceFailure):
                        physical_usage = exc.usage_record
                    error = f"{type(exc).__name__}: {exc}"
                    raise
                finally:
                    usage = UsageRecord(
                        input_tokens=(
                            physical_usage.input_tokens if physical_usage is not None else metadata.get("input_tokens")
                        ),
                        output_tokens=(
                            physical_usage.output_tokens
                            if physical_usage is not None
                            else metadata.get("output_tokens")
                        ),
                        model_calls=physical_usage.model_calls if physical_usage is not None else 1,
                        monetary_cost=(
                            physical_usage.monetary_cost
                            if physical_usage is not None
                            else metadata.get("reference_cost")
                        ),
                        paid_monetary_cost=(
                            physical_usage.paid_monetary_cost
                            if physical_usage is not None
                            else metadata.get("total_cost")
                        ),
                        wall_seconds=time.perf_counter() - started,
                        model_name=self.model_string,
                        raw_metadata={
                            **metadata,
                            "system_prompt": system_prompt,
                            "seed_forwarded": supports_seed,
                            "decoding_configuration": dict(getattr(config, f"{self.role}_decoding")),
                        },
                    )
                    attempts.append(
                        OptimizationAttempt(
                            self.stage,
                            index,
                            self.model_string,
                            prompt,
                            content,
                            usage,
                            generation_seed=seed,
                            supports_seed=supports_seed,
                            error=error,
                        )
                    )

        def work(records: Sequence[PromptOccurrence], *, planning: bool = False) -> tuple[str, TextualGradient]:
            # A dry graph uses the pinned library's exact input construction and
            # symbolic output reservations. It never proposes a revision or
            # invokes TGD.step; the actual graph below does each exactly once.
            symbols: list[tuple[str, str]] = []

            def check_plan(prompt: str, system_prompt: str | None, role: str) -> None:
                controller = controllers[role]
                messages = [{"role": "system", "content": system_prompt or ""}, {"role": "user", "content": prompt}]
                count = controller.counter.count_input(messages)
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
                    check_plan(prompt, system_prompt, self.role)
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
                    check_plan(optimizer._update_prompt(variable), optimizer.optimizer_system_prompt, "optimizer")
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
        except asyncio.CancelledError:
            cancelled.set()
            with pending_lock:
                for future in tuple(pending):
                    future.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except Exception as exc:
            cancelled.set()
            with pending_lock:
                futures = tuple(pending)
                for future in futures:
                    future.cancel()
            # Drain the finite local graph so no late engine calls or audit writes
            # remain after the iteration has been recorded as unsuccessful.
            await asyncio.gather(task, return_exceptions=True)
            return PromptProposal(
                None,
                attempts=tuple(attempts),
                error=f"{type(exc).__name__}: {exc}",
                metadata={
                    **self.metadata(),
                    **selection_metadata,
                    "budget_exhausted": isinstance(exc, BudgetExhausted),
                },
            )
