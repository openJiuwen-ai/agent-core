# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publicly reusable capability fingerprint build service."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error, raise_error
from openjiuwen.core.common.logging import logger
from openjiuwen.symphony.evaluation import EvaluationSuite
from openjiuwen.symphony.interfaces import CapabilityProvider, SymphonyLLM
from openjiuwen.symphony.models import (
    CapabilityDescriptor,
    CapabilityFingerprint,
    EvaluationCase,
    EvidenceRef,
    FailureReason,
    FingerprintArtifact,
    ImprovementSuggestion,
    MetricResult,
    MetricStatus,
    NormalizationDecision,
    NormalizationIssue,
    QualityResult,
    SourceSnapshot,
)
from openjiuwen.symphony.shared.fingerprint.artifact import FingerprintArtifactStore
from openjiuwen.symphony.shared.fingerprint.cache import FingerprintCache
from openjiuwen.symphony.shared.fingerprint.extractor import (
    CapabilityFingerprintExtractor,
    ExtractionOutcome,
    capability_content_hash,
)
from openjiuwen.symphony.shared.fingerprint.normalization import (
    IONameVocabulary,
    build_io_name_vocabulary,
    normalize_capability_type,
)
from openjiuwen.symphony.shared.fingerprint.settings import FingerprintSettings


class QualityEvaluationSuite(Protocol):
    """Narrow service dependency implemented by ``EvaluationSuite``."""

    async def evaluate(
        self,
        fingerprint: CapabilityFingerprint,
        cases: Sequence[EvaluationCase] = (),
    ) -> QualityResult:
        """Evaluate one fingerprint and its explicitly supplied trace cases."""

        ...


@dataclass(frozen=True, slots=True)
class _ExtractionCacheQuery:
    """Inputs that jointly identify one reusable extraction result."""

    content_hash: str
    descriptor_signature: str
    normalization_signature: str | None
    io_name_vocabulary: IONameVocabulary


class FingerprintService:
    """Build, evaluate, atomically publish, and read capability fingerprints."""

    def __init__(
        self,
        capability_provider: CapabilityProvider,
        artifact_root: str | Path,
        *,
        llm: SymphonyLLM | None = None,
        settings: FingerprintSettings | None = None,
        evaluation_suite: QualityEvaluationSuite | None = None,
    ) -> None:
        self._provider = capability_provider
        self._llm = llm
        self._settings = settings or FingerprintSettings()
        self._store = FingerprintArtifactStore(artifact_root)
        self._cache = FingerprintCache(self._store.root)
        self._extractor = CapabilityFingerprintExtractor(self._settings, llm)
        self._evaluation = evaluation_suite or EvaluationSuite.default(
            llm=llm,
            enable_llm=self._settings.enable_llm_evaluation,
            evidence_text_limit=self._settings.evidence_text_limit,
        )
        self._build_lock = asyncio.Lock()
        self._process_lock = FileLock(str(self._store.root / ".fingerprint.lock"))
        self._cancel_requested = False

    @property
    def is_building(self) -> bool:
        """Whether this service instance currently owns a build."""

        return self._build_lock.locked()

    async def build(
        self,
        *,
        force: bool = False,
        traces: Sequence[EvaluationCase | dict[str, Any]] = (),
    ) -> FingerprintArtifact:
        """Build and publish ``fingerprint.json`` from the provider snapshot."""

        self._validate_configuration()
        if self._build_lock.locked():
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_BUILD_STATE_INVALID,
                reason="a fingerprint build is already running in this service",
            )

        async with self._build_lock:
            self._cancel_requested = False
            started_at = time.monotonic()
            lock_acquired = False
            try:
                self._store.root.mkdir(parents=True, exist_ok=True)
                try:
                    self._process_lock.acquire(timeout=0)
                    lock_acquired = True
                except FileLockTimeout as exc:
                    raise_error(
                        StatusCode.COMPONENT_SYMPHONY_BUILD_STATE_INVALID,
                        cause=exc,
                        reason="another fingerprint builder owns the artifact root",
                    )

                snapshot, descriptors = await self._load_inventory()
                cases = _validate_cases(traces)
                descriptor_vocabulary = build_io_name_vocabulary(descriptors)
                base_signature = self._cache_base_signature()
                restored_vocabulary = IONameVocabulary.empty()
                cached: dict[str, dict[str, Any]] = {}
                if self._settings.cache_enabled and base_signature is not None:
                    cache_snapshot = self._cache.load(base_signature)
                    restored_vocabulary = cache_snapshot.io_name_vocabulary
                    if not force:
                        cached = cache_snapshot.entries
                io_name_vocabulary = descriptor_vocabulary.merge(restored_vocabulary)
                normalization_signature = _normalization_cache_signature(base_signature, io_name_vocabulary)
                outcomes, reused_count, descriptor_signatures, io_name_vocabulary = await self._extract(
                    descriptors,
                    cached,
                    io_name_vocabulary,
                    normalization_signature,
                )
                fingerprints = [outcome.fingerprint for outcome in outcomes]
                self._raise_if_cancelled()

                evaluated: list[CapabilityFingerprint] = []
                evaluation_signatures: dict[str, str | None] = {}
                quality_reused_count = 0
                suite_signature = self._quality_suite_signature()
                for fingerprint in fingerprints:
                    self._raise_if_cancelled()
                    capability_cases = tuple(case for case in cases if _case_references(case, fingerprint))
                    evaluation_signature = _evaluation_cache_signature(
                        protocol=self._settings.evaluation_protocol_version,
                        suite_signature=suite_signature,
                        fingerprint=fingerprint,
                        cases=capability_cases,
                    )
                    evaluation_signatures[fingerprint.capability_id] = evaluation_signature
                    cached_evaluated = _cached_evaluated_fingerprint(
                        cached.get(fingerprint.capability_id),
                        fingerprint,
                        evaluation_signature,
                    )
                    if cached_evaluated is not None:
                        evaluated.append(cached_evaluated)
                        quality_reused_count += 1
                        continue
                    try:
                        raw_quality = await self._evaluation.evaluate(fingerprint, capability_cases)
                        quality = QualityResult.model_validate(raw_quality)
                    except Exception as exc:  # noqa: BLE001 -- registered evaluators are an extension boundary.
                        quality = _evaluation_error_quality(fingerprint, exc)
                    if (quality.capability_id, quality.capability_type) != (
                        fingerprint.capability_id,
                        fingerprint.capability_type,
                    ):
                        quality = _evaluation_error_quality(
                            fingerprint,
                            error_type="QualityIdentityMismatch",
                        )
                    evaluated.append(_with_quality(fingerprint, quality))

                self._raise_if_cancelled()
                artifact = FingerprintArtifact(
                    source_snapshot=snapshot,
                    fingerprints=tuple(sorted(evaluated, key=lambda item: (item.capability_type, item.capability_id))),
                )
                await _run_noninterruptible_thread(self._store.publish, artifact)
                outcomes_by_id = {outcome.fingerprint.capability_id: outcome for outcome in outcomes}
                normalization_signature = _normalization_cache_signature(base_signature, io_name_vocabulary)
                cache_entries = {}
                for item in artifact.fingerprints:
                    extraction = outcomes_by_id[item.capability_id]
                    cache_entries[item.capability_id] = {
                        "content_hash": item.content_hash,
                        "descriptor_signature": descriptor_signatures[item.capability_id],
                        "normalization_signature": normalization_signature,
                        "fingerprint": extraction.fingerprint.model_dump(mode="json"),
                        "evaluated_fingerprint": item.model_dump(mode="json"),
                        "evaluation_signature": evaluation_signatures[item.capability_id],
                        "normalization": {
                            "decisions": [
                                decision.model_dump(mode="json") for decision in extraction.normalization_decisions
                            ],
                            "issues": [issue.model_dump(mode="json") for issue in extraction.normalization_issues],
                            "io_name_vocabulary_version": io_name_vocabulary.version,
                            "io_name_vocabulary_hash": io_name_vocabulary.content_hash,
                            "inputs": extraction.normalization_inputs,
                            "outputs": extraction.normalization_outputs,
                        },
                    }
                if self._settings.cache_enabled and base_signature is not None:
                    try:
                        await _run_noninterruptible_thread(
                            self._cache.publish,
                            base_signature,
                            cache_entries,
                            io_name_vocabulary,
                        )
                    except (OSError, TypeError, ValueError, BaseError) as exc:
                        logger.warning("Symphony fingerprint cache publish failed: %s", type(exc).__name__)

                elapsed_ms = round((time.monotonic() - started_at) * 1000)
                failure_count = sum(len(item.failures) for item in artifact.fingerprints)
                skipped_count = _count_skipped_metrics(artifact.fingerprints)
                logger.info(
                    "Symphony fingerprint build completed: schema=%s snapshot=%s capabilities=%s reused=%s "
                    "quality_reused=%s failures=%s skipped=%s elapsed_ms=%s",
                    artifact.schema_version,
                    snapshot.snapshot_id,
                    len(artifact.fingerprints),
                    reused_count,
                    quality_reused_count,
                    failure_count,
                    skipped_count,
                    elapsed_ms,
                )
                return artifact
            except OSError as exc:
                raise build_error(
                    StatusCode.COMPONENT_SYMPHONY_ARTIFACT_WRITE_CALL_FAILED,
                    cause=exc,
                    reason=type(exc).__name__,
                ) from exc
            finally:
                if lock_acquired:
                    self._process_lock.release()

    def read(self) -> FingerprintArtifact:
        """Read and validate the most recently published artifact."""

        return self._store.read()

    def cancel_build(self) -> bool:
        """Request cooperative cancellation before the next atomic publish."""

        if not self._build_lock.locked():
            return False
        self._cancel_requested = True
        return True

    async def _load_inventory(self) -> tuple[SourceSnapshot, list[CapabilityDescriptor]]:
        atomic_loader = getattr(self._provider, "inventory_snapshot", None)
        inventory_snapshot: Any = None
        raw_snapshot: Any = None
        raw_descriptors: Any = None
        raw_snapshot_after: Any = None
        try:
            if callable(atomic_loader):
                inventory_snapshot = await atomic_loader()
            else:
                raw_snapshot = await self._provider.source_snapshot()
                raw_descriptors = await self._provider.capabilities()
                raw_snapshot_after = await self._provider.source_snapshot()
        except Exception as exc:  # noqa: BLE001 -- providers may expose application-specific failures.
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_BUILD_RUNTIME_ERROR,
                cause=exc,
                reason=f"capability provider failed ({type(exc).__name__})",
            )
        if callable(atomic_loader):
            if not isinstance(inventory_snapshot, tuple) or len(inventory_snapshot) != 2:
                raise_error(
                    StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                    reason="inventory_snapshot must return a two-item tuple",
                )
            raw_snapshot, raw_descriptors = inventory_snapshot
            raw_snapshot_after = raw_snapshot
        try:
            snapshot = SourceSnapshot.model_validate(raw_snapshot)
            snapshot_after = SourceSnapshot.model_validate(raw_snapshot_after)
            descriptors = [_canonical_descriptor(CapabilityDescriptor.model_validate(item)) for item in raw_descriptors]
        except (PydanticValidationError, TypeError) as exc:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                cause=exc,
                reason="provider returned an invalid capability inventory",
            )
        if _snapshot_stability_payload(snapshot) != _snapshot_stability_payload(snapshot_after):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                reason="provider snapshot changed while the capability inventory was being read",
            )
        identities = [item.capability_id for item in descriptors]
        if len(identities) != len(set(identities)):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                reason="capability_id must be unique within a provider snapshot",
            )
        if snapshot.capability_count is not None and snapshot.capability_count != len(descriptors):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                reason="source snapshot capability_count does not match the inventory",
            )
        return snapshot, descriptors

    async def _extract(
        self,
        descriptors: Sequence[CapabilityDescriptor],
        cached: dict[str, dict[str, Any]],
        io_name_vocabulary: IONameVocabulary,
        normalization_signature: str | None,
    ) -> tuple[list[ExtractionOutcome], int, dict[str, str], IONameVocabulary]:
        resolved: dict[str, ExtractionOutcome] = {}
        changed: list[CapabilityDescriptor] = []
        descriptor_signatures: dict[str, str] = {}
        reused_count = 0
        for descriptor in descriptors:
            content_hash = descriptor.content_hash or capability_content_hash(descriptor)
            descriptor_signature = capability_content_hash(descriptor)
            descriptor_signatures[descriptor.capability_id] = descriptor_signature
            entry = cached.get(descriptor.capability_id)
            cached_outcome = _cached_extraction(
                entry,
                descriptor,
                _ExtractionCacheQuery(
                    content_hash=content_hash,
                    descriptor_signature=descriptor_signature,
                    normalization_signature=normalization_signature,
                    io_name_vocabulary=io_name_vocabulary,
                ),
            )
            if cached_outcome is None:
                changed.append(descriptor.model_copy(update={"content_hash": content_hash}))
            else:
                resolved[descriptor.capability_id] = cached_outcome
                reused_count += 1

        outcomes = await self._extractor.extract_many(
            changed,
            io_name_vocabulary=io_name_vocabulary,
            is_cancelled=lambda: self._cancel_requested,
        )
        if len(outcomes) != len(changed):
            self._raise_if_cancelled()
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_BUILD_RUNTIME_ERROR,
                reason="fingerprint extraction did not return one result per changed capability",
            )
        resolved.update({outcome.fingerprint.capability_id: outcome for outcome in outcomes})
        final_vocabulary = io_name_vocabulary.merge(*(outcome.io_name_vocabulary for outcome in outcomes))
        reconciled = {
            capability_id: self._extractor.renormalize(outcome, final_vocabulary)
            for capability_id, outcome in resolved.items()
        }
        return (
            [reconciled[item.capability_id] for item in descriptors],
            reused_count,
            descriptor_signatures,
            final_vocabulary,
        )

    def _validate_configuration(self) -> None:
        settings = self._settings
        boolean_values = (
            settings.enable_llm_extraction,
            settings.enable_llm_evaluation,
            settings.cache_enabled,
        )
        if any(not isinstance(value, bool) for value in boolean_values):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="LLM and cache feature flags must be booleans",
            )
        positive_integers = (
            settings.batch_size,
            settings.max_concurrency,
            settings.llm_max_tokens,
            settings.evidence_text_limit,
        )
        if any(not _is_positive_integer(value) for value in positive_integers):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="batch, concurrency, token, and evidence limits must be positive integers",
            )
        if not _is_nonnegative_integer(settings.body_limit):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="body_limit must be a non-negative integer",
            )
        timeout = settings.llm_timeout
        if timeout is not None and not _is_positive_finite_number(timeout):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="llm_timeout must be a positive finite number when configured",
            )
        if (settings.enable_llm_extraction or settings.enable_llm_evaluation) and self._llm is None:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="an LLM is required by the enabled fingerprint settings",
            )
        protocol_values = (
            settings.extraction_protocol_version,
            settings.evaluation_protocol_version,
        )
        if any(not isinstance(value, str) or not value.strip() for value in protocol_values):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="extraction and evaluation protocol versions must be non-empty strings",
            )
        if not isinstance(settings.configuration_signature, str):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="configuration_signature must be a string",
            )

    def _quality_suite_signature(self) -> str | None:
        return _extension_signature(self._evaluation)

    def _cache_base_signature(self) -> str | None:
        settings_signature = self._settings.signature()
        llm_signature = "disabled"
        if self._settings.enable_llm_extraction:
            llm_signature = self._settings.configuration_signature.strip() or _extension_signature(self._llm) or ""
            if not llm_signature:
                return None
        canonical = json.dumps(
            {
                "settings_signature": settings_signature,
                "llm_signature": llm_signature,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_BUILD_INTERRUPTED,
                reason="fingerprint build was cancelled before publication",
            )


def _snapshot_stability_payload(snapshot: SourceSnapshot) -> dict[str, Any]:
    """Compare provider identity fields while allowing a fresh observation timestamp."""

    return snapshot.model_dump(mode="json", exclude={"captured_at"})


def _canonical_descriptor(descriptor: CapabilityDescriptor) -> CapabilityDescriptor:
    capability_type = normalize_capability_type(descriptor.capability_type)
    if capability_type == descriptor.capability_type:
        return descriptor
    return descriptor.model_copy(update={"capability_type": capability_type})


def _validate_cases(raw_cases: Sequence[EvaluationCase | dict[str, Any]]) -> tuple[EvaluationCase, ...]:
    try:
        cases: list[EvaluationCase] = []
        for item in raw_cases:
            case = EvaluationCase.model_validate(item)
            calls = tuple(
                call.model_copy(update={"capability_type": normalize_capability_type(call.capability_type)})
                for call in case.calls
            )
            cases.append(
                EvaluationCase.model_validate(
                    case.model_copy(
                        update={
                            "capability_type": normalize_capability_type(case.capability_type),
                            "calls": calls,
                        }
                    )
                )
            )
        return tuple(cases)
    except (PydanticValidationError, TypeError) as exc:
        raise build_error(
            StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
            cause=exc,
            reason="evaluation traces are invalid",
        ) from exc


def _normalization_cache_signature(
    base_signature: str | None,
    io_name_vocabulary: IONameVocabulary,
) -> str | None:
    if base_signature is None:
        return None
    canonical = json.dumps(
        {
            "base_signature": base_signature,
            "io_name_vocabulary_version": io_name_vocabulary.version,
            "io_name_vocabulary_hash": io_name_vocabulary.content_hash,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cached_extraction(
    entry: dict[str, Any] | None,
    descriptor: CapabilityDescriptor,
    query: _ExtractionCacheQuery,
) -> ExtractionOutcome | None:
    if not isinstance(entry, dict) or entry.get("content_hash") != query.content_hash:
        return None
    if entry.get("descriptor_signature") != query.descriptor_signature:
        return None
    if query.normalization_signature is None:
        return None
    if entry.get("normalization_signature") != query.normalization_signature:
        return None
    try:
        fingerprint = CapabilityFingerprint.model_validate(entry.get("fingerprint"))
    except (PydanticValidationError, TypeError):
        return None
    if fingerprint.capability_id != descriptor.capability_id:
        return None
    if fingerprint.capability_type != normalize_capability_type(descriptor.capability_type):
        return None
    if fingerprint.quality is not None:
        return None
    normalization = entry.get("normalization")
    if not isinstance(normalization, dict):
        return None
    if normalization.get("io_name_vocabulary_version") != query.io_name_vocabulary.version:
        return None
    if normalization.get("io_name_vocabulary_hash") != query.io_name_vocabulary.content_hash:
        return None
    raw_decisions = normalization.get("decisions")
    raw_issues = normalization.get("issues")
    if not isinstance(raw_decisions, list) or not isinstance(raw_issues, list):
        return None
    if "inputs" not in normalization or "outputs" not in normalization:
        return None
    try:
        decisions = tuple(NormalizationDecision.model_validate(item) for item in raw_decisions)
        issues = tuple(NormalizationIssue.model_validate(item) for item in raw_issues)
    except (PydanticValidationError, TypeError):
        return None
    return ExtractionOutcome(
        fingerprint=fingerprint,
        io_name_vocabulary=query.io_name_vocabulary,
        normalization_decisions=decisions,
        normalization_issues=issues,
        normalization_inputs=normalization["inputs"],
        normalization_outputs=normalization["outputs"],
    )


def _is_positive_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(float(value)) and value > 0


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _count_skipped_metrics(fingerprints: Sequence[CapabilityFingerprint]) -> int:
    skipped_count = 0
    for fingerprint in fingerprints:
        if fingerprint.quality is None:
            continue
        skipped_count += sum(metric.status == MetricStatus.NOT_APPLICABLE for metric in fingerprint.quality.metrics)
    return skipped_count


def _cached_evaluated_fingerprint(
    entry: dict[str, Any] | None,
    fingerprint: CapabilityFingerprint,
    evaluation_signature: str | None,
) -> CapabilityFingerprint | None:
    if evaluation_signature is None or not isinstance(entry, dict):
        return None
    if entry.get("content_hash") != fingerprint.content_hash:
        return None
    if entry.get("evaluation_signature") != evaluation_signature:
        return None
    try:
        evaluated = CapabilityFingerprint.model_validate(entry.get("evaluated_fingerprint"))
    except (PydanticValidationError, TypeError):
        return None
    if evaluated.quality is None:
        return None
    if (evaluated.capability_id, evaluated.capability_type, evaluated.content_hash) != (
        fingerprint.capability_id,
        fingerprint.capability_type,
        fingerprint.content_hash,
    ):
        return None
    return evaluated


def _evaluation_cache_signature(
    *,
    protocol: str,
    suite_signature: str | None,
    fingerprint: CapabilityFingerprint,
    cases: Sequence[EvaluationCase],
) -> str | None:
    if suite_signature is None:
        return None
    payload = {
        "protocol": protocol,
        "suite_signature": suite_signature,
        "fingerprint": fingerprint.model_dump(mode="json"),
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _case_references(case: EvaluationCase, fingerprint: CapabilityFingerprint) -> bool:
    identity = (fingerprint.capability_id, normalize_capability_type(fingerprint.capability_type))
    if (case.capability_id, normalize_capability_type(case.capability_type)) == identity:
        return True
    return any((call.capability_id, normalize_capability_type(call.capability_type)) == identity for call in case.calls)


def _with_quality(fingerprint: CapabilityFingerprint, quality: QualityResult) -> CapabilityFingerprint:
    metrics: tuple[MetricResult, ...] = quality.metrics
    evidence = _unique_models((*fingerprint.evidence, *(item for metric in metrics for item in metric.evidence)))
    failures = _unique_models((*fingerprint.failures, *(item for metric in metrics for item in metric.failures)))
    suggestions = _unique_models(
        (*fingerprint.suggestions, *(item for metric in metrics for item in metric.suggestions))
    )
    return fingerprint.model_copy(
        update={
            "quality": quality,
            "evidence": evidence,
            "failures": failures,
            "suggestions": suggestions,
        }
    )


def _evaluation_error_quality(
    fingerprint: CapabilityFingerprint,
    exc: BaseException | None = None,
    *,
    error_type: str | None = None,
) -> QualityResult:
    failure_type = error_type or (type(exc).__name__ if exc is not None else "UnknownEvaluationError")
    evidence = EvidenceRef(
        evidence_type="evaluation_error",
        reference=f"capability:{fingerprint.capability_id}",
        description="The evaluation suite failed without exposing trace payloads.",
    )
    failure = FailureReason(
        code="EVALUATION_SUITE_FAILED",
        message="Capability evaluation failed.",
        evidence=(evidence,),
        details={"exception_type": failure_type},
    )
    suggestion = ImprovementSuggestion(
        code="CHECK_EVALUATION_SUITE",
        message="Check registered evaluators and retry the fingerprint build.",
        related_failures=(failure.code,),
    )
    metric = MetricResult(
        metric_id="evaluation_suite",
        capability_id=fingerprint.capability_id,
        capability_type=fingerprint.capability_type,
        status=MetricStatus.ERROR,
        reason="Capability evaluation failed.",
        evidence=(evidence,),
        failures=(failure,),
        suggestions=(suggestion,),
    )
    return QualityResult(
        capability_id=fingerprint.capability_id,
        capability_type=fingerprint.capability_type,
        metrics=(metric,),
    )


def _unique_models(values: Sequence[Any]) -> tuple[Any, ...]:
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return tuple(unique)


async def _run_noninterruptible_thread(function: Callable[..., Any], *args: Any) -> Any:
    """Keep the process lock until an in-flight atomic writer has finished."""

    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError as exc:
            cancellation = exc
    if cancellation is not None:
        raise cancellation
    return result


def _extension_signature(extension: object) -> str | None:
    try:
        provider = getattr(extension, "cache_signature", None)
        if inspect.iscoroutinefunction(provider):
            return None
        value = provider() if callable(provider) else provider
    except Exception:  # noqa: BLE001 -- opaque extensions disable cache reuse.
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["FingerprintService"]
