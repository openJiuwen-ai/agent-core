# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capability semantic extraction with deterministic and optional LLM stages."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from json_repair import loads as repair_json_loads

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.interfaces import SymphonyLLM
from openjiuwen.symphony.models import (
    CapabilityDescriptor,
    CapabilityFingerprint,
    EvidenceRef,
    FailureReason,
    ImprovementSuggestion,
    NormalizationDecision,
    NormalizationIssue,
    SemanticProfile,
    SuggestionPriority,
)
from openjiuwen.symphony.models._redaction import redact_sensitive_json
from openjiuwen.symphony.shared.fingerprint.normalization import (
    IONameVocabulary,
    build_io_name_vocabulary,
    normalize_capability_type,
    normalize_io_specs,
    sanitize_io_specs,
)
from openjiuwen.symphony.shared.fingerprint.settings import FingerprintSettings

_EXTRACTION_SYSTEM_PROMPT = """Extract a capability fingerprint from the supplied capability definition.

Return one JSON object with these fields:
{
  "description": "concise factual summary",
  "semantic_profile": {
    "summary": "retrieval summary",
    "capabilities": ["atomic capability"],
    "use_cases": ["supported use case"],
    "limitations": ["documented limitation"],
    "keywords": ["search keyword"]
  },
  "inputs": [{"name": "semantic_name", "type": "data_type", "required": true,
              "description": "short description"}],
  "outputs": [{"name": "semantic_name", "type": "data_type",
               "description": "short description"}],
  "classification": "classification path",
  "tags": ["normalized tag"]
}

Use only evidence present in the supplied definition. Inputs are caller-provided runtime values; exclude credentials,
permissions, caches, telemetry, and internal state. Outputs are user-facing or downstream artifacts. Preserve media
semantics instead of classifying an image/audio/video URL as a generic URL. Return JSON only.
"""


@dataclass(frozen=True)
class ExtractionOutcome:
    """One isolated extraction result."""

    fingerprint: CapabilityFingerprint
    io_name_vocabulary: IONameVocabulary
    normalization_decisions: tuple[NormalizationDecision, ...] = ()
    normalization_issues: tuple[NormalizationIssue, ...] = ()
    normalization_inputs: object = None
    normalization_outputs: object = None
    used_llm: bool = False


class CapabilityFingerprintExtractor:
    """Build semantic fingerprints while isolating per-capability failures."""

    def __init__(self, settings: FingerprintSettings, llm: SymphonyLLM | None = None) -> None:
        self._settings = settings
        self._llm = llm

    async def extract_many(
        self,
        descriptors: Sequence[CapabilityDescriptor],
        *,
        io_name_vocabulary: IONameVocabulary | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_complete: Callable[[ExtractionOutcome], Any] | None = None,
    ) -> list[ExtractionOutcome]:
        """Extract in bounded batches, preserving inventory order."""

        return await self._extract_many(
            descriptors,
            io_name_vocabulary=io_name_vocabulary,
            is_cancelled=is_cancelled,
            on_complete=on_complete,
        )

    async def _extract_many(
        self,
        descriptors: Sequence[CapabilityDescriptor],
        *,
        io_name_vocabulary: IONameVocabulary | None,
        is_cancelled: Callable[[], bool] | None,
        on_complete: Callable[[ExtractionOutcome], Any] | None,
    ) -> list[ExtractionOutcome]:
        """Internal extraction path with an optional completion observer."""

        cancelled = is_cancelled or (lambda: False)
        name_vocabulary = io_name_vocabulary or build_io_name_vocabulary(descriptors)
        results: list[ExtractionOutcome] = []
        batch_size = max(1, self._settings.batch_size)
        semaphore = asyncio.Semaphore(max(1, self._settings.max_concurrency))

        async def extract_bounded(descriptor: CapabilityDescriptor) -> ExtractionOutcome:
            async with semaphore:
                try:
                    outcome = await self.extract(descriptor, io_name_vocabulary=name_vocabulary)
                except BaseError:
                    # Configuration and other framework-wide failures must abort publication.
                    raise
                except Exception as exc:  # noqa: BLE001 -- isolate one malformed capability from its peers.
                    outcome = _failed_extraction_outcome(descriptor, exc, name_vocabulary)
                if on_complete is not None:
                    result = on_complete(outcome)
                    if inspect.isawaitable(result):
                        await result
                return outcome

        for start in range(0, len(descriptors), batch_size):
            if cancelled():
                break
            batch_end = start + batch_size
            batch = descriptors[start:batch_end]
            results.extend(await asyncio.gather(*(extract_bounded(item) for item in batch)))
        return results

    async def extract(
        self,
        descriptor: CapabilityDescriptor,
        *,
        io_name_vocabulary: IONameVocabulary | None = None,
    ) -> ExtractionOutcome:
        """Extract one capability, retaining a usable static result on LLM failure."""

        descriptor = CapabilityDescriptor.model_validate(descriptor)
        name_vocabulary = io_name_vocabulary or build_io_name_vocabulary((descriptor,))
        content_hash = descriptor.content_hash or capability_content_hash(descriptor)
        payload: dict[str, Any] = {}
        failures: list[FailureReason] = []
        suggestions: list[ImprovementSuggestion] = []
        used_llm = False

        if self._settings.enable_llm_extraction:
            try:
                response = await self._invoke_llm(descriptor)
                parsed = _response_payload(response)
                if parsed is None:
                    failures.append(_extraction_failure("LLM_RESPONSE_INVALID", "LLM extraction returned invalid JSON"))
                    suggestions.append(
                        ImprovementSuggestion(
                            code="RETRY_LLM_EXTRACTION",
                            message="Retry semantic extraction with a JSON-capable model.",
                            priority=SuggestionPriority.LOW,
                            related_failures=("LLM_RESPONSE_INVALID",),
                        )
                    )
                else:
                    payload = parsed
                    used_llm = True
            except BaseError:
                # Framework model failures must abort the enclosing build.
                raise
            except Exception as exc:  # noqa: BLE001 -- injected LLMs expose provider-specific failures.
                failures.append(
                    FailureReason(
                        code="LLM_EXTRACTION_FAILED",
                        message="LLM semantic extraction failed.",
                        details={"exception_type": type(exc).__name__},
                    )
                )
                suggestions.append(
                    ImprovementSuggestion(
                        code="CHECK_LLM_EXTRACTION",
                        message="Check the injected LLM and retry semantic extraction.",
                        priority=SuggestionPriority.MEDIUM,
                        related_failures=("LLM_EXTRACTION_FAILED",),
                    )
                )

        profile = _semantic_profile(descriptor, payload)
        raw_inputs: object = (
            [item.model_dump(mode="json") for item in descriptor.inputs] if descriptor.inputs else payload.get("inputs")
        )
        raw_outputs: object = (
            [item.model_dump(mode="json") for item in descriptor.outputs]
            if descriptor.outputs
            else payload.get("outputs")
        )
        normalization_inputs = sanitize_io_specs(raw_inputs)
        normalization_outputs = sanitize_io_specs(raw_outputs)
        learned_vocabulary = name_vocabulary.with_observed_specs(normalization_inputs, normalization_outputs)
        input_normalization = normalize_io_specs(
            normalization_inputs,
            direction="input",
            io_name_vocabulary=learned_vocabulary,
            include_audit=True,
        )
        output_normalization = normalize_io_specs(
            normalization_outputs,
            direction="output",
            io_name_vocabulary=learned_vocabulary,
            include_audit=True,
        )
        description = descriptor.description or _text(payload.get("description"))
        classification = descriptor.classification or _classification(payload.get("classification"))
        tags = _deduplicate((*descriptor.tags, *_strings(payload.get("tags"))))
        evidence = (
            EvidenceRef(
                evidence_type="capability_definition",
                reference=f"capability:{descriptor.capability_id}",
                description="Provider-supplied capability metadata and semantic definition.",
                metadata={"content_hash": content_hash},
            ),
        )
        fingerprint = CapabilityFingerprint(
            capability_id=descriptor.capability_id,
            capability_type=normalize_capability_type(descriptor.capability_type),
            name=descriptor.name,
            description=description,
            source=descriptor.source,
            available=descriptor.available,
            inputs=input_normalization.items,
            outputs=output_normalization.items,
            classification=classification,
            tags=tags,
            content_hash=content_hash,
            semantic_profile=profile,
            failures=tuple(failures),
            evidence=evidence,
            suggestions=tuple(suggestions),
            metadata=descriptor.metadata,
        )
        return ExtractionOutcome(
            fingerprint=fingerprint,
            io_name_vocabulary=learned_vocabulary,
            normalization_decisions=(*input_normalization.decisions, *output_normalization.decisions),
            normalization_issues=(*input_normalization.issues, *output_normalization.issues),
            normalization_inputs=normalization_inputs,
            normalization_outputs=normalization_outputs,
            used_llm=used_llm,
        )

    @staticmethod
    def renormalize(
        outcome: ExtractionOutcome,
        io_name_vocabulary: IONameVocabulary,
    ) -> ExtractionOutcome:
        """Reconcile one concurrent extraction against the deterministic final vocabulary."""

        if outcome.io_name_vocabulary.content_hash == io_name_vocabulary.content_hash:
            return outcome
        fingerprint = outcome.fingerprint
        inputs = normalize_io_specs(
            outcome.normalization_inputs,
            direction="input",
            io_name_vocabulary=io_name_vocabulary,
            include_audit=True,
        )
        outputs = normalize_io_specs(
            outcome.normalization_outputs,
            direction="output",
            io_name_vocabulary=io_name_vocabulary,
            include_audit=True,
        )
        normalized_fingerprint = CapabilityFingerprint.model_validate(
            fingerprint.model_copy(update={"inputs": inputs.items, "outputs": outputs.items})
        )
        return ExtractionOutcome(
            fingerprint=normalized_fingerprint,
            io_name_vocabulary=io_name_vocabulary,
            normalization_decisions=(*inputs.decisions, *outputs.decisions),
            normalization_issues=(*inputs.issues, *outputs.issues),
            normalization_inputs=outcome.normalization_inputs,
            normalization_outputs=outcome.normalization_outputs,
            used_llm=outcome.used_llm,
        )

    async def _invoke_llm(self, descriptor: CapabilityDescriptor) -> object:
        llm = self._llm
        if llm is None:
            # FingerprintService validates this global configuration before a build.
            return ""
        semantic_definition = _without_frontmatter(descriptor.semantic_content)
        context = {
            "capability_id": descriptor.capability_id,
            "capability_type": descriptor.capability_type,
            "name": descriptor.name,
            "description": descriptor.description,
            "declared_inputs": [item.model_dump(mode="json") for item in descriptor.inputs],
            "declared_outputs": [item.model_dump(mode="json") for item in descriptor.outputs],
            "classification": descriptor.classification,
            "tags": list(descriptor.tags),
            "definition": semantic_definition[: max(0, self._settings.body_limit)],
            "definition_truncated": len(semantic_definition) > max(0, self._settings.body_limit),
        }
        safe_context = redact_sensitive_json(context)
        return await llm.invoke(
            [
                {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(safe_context, ensure_ascii=False, separators=(",", ":"))},
            ],
            temperature=0.0,
            max_tokens=self._settings.llm_max_tokens,
            timeout=self._settings.llm_timeout,
        )


def _without_frontmatter(value: str) -> str:
    """Exclude frontmatter because normalized descriptor fields already represent it."""

    lines = value.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return value
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") in {"---", "..."}:
            body_start = index + 1
            return "".join(lines[body_start:])
    return value


def capability_content_hash(descriptor: CapabilityDescriptor) -> str:
    """Hash all provider-visible semantic fields when no asset hash is supplied."""

    payload = descriptor.model_dump(mode="json", exclude={"content_hash"})
    # ``semantic_content`` is intentionally excluded from public model
    # serialization, but it still participates in incremental invalidation.
    payload["semantic_content"] = descriptor.semantic_content
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _semantic_profile(descriptor: CapabilityDescriptor, payload: dict[str, Any]) -> SemanticProfile:
    raw_profile = payload.get("semantic_profile")
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    summary = _text(profile.get("summary")) or descriptor.description or descriptor.name
    return SemanticProfile(
        summary=summary,
        capabilities=_strings(profile.get("capabilities")),
        use_cases=_strings(profile.get("use_cases")),
        limitations=_strings(profile.get("limitations")),
        keywords=_deduplicate((*descriptor.tags, *_strings(profile.get("keywords")))),
    )


def _classification(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    values = _strings(value)
    return values[0] if values else ""


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, list | tuple):
        return ()
    texts = (_text(item) for item in value)
    return tuple(text for text in texts if text)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _deduplicate(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _parse_json_object(content: str) -> dict[str, Any] | None:
    if not content.strip():
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        try:
            payload = repair_json_loads(content)
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def _response_payload(response: object) -> dict[str, Any] | None:
    parser_content = getattr(response, "parser_content", None)
    if parser_content is not None:
        candidate = parser_content
    elif hasattr(response, "content"):
        candidate = response.content
    else:
        candidate = response
    if isinstance(candidate, Mapping):
        return dict(candidate)
    model_dump = getattr(candidate, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return _parse_json_object(_response_text(candidate))


def _response_text(response: object) -> str:
    if isinstance(response, str):
        return response
    if not isinstance(response, list | tuple):
        return ""
    parts: list[str] = []
    for item in response:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            parts.append(item["text"])
        else:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _failed_extraction_outcome(
    descriptor: CapabilityDescriptor,
    exc: Exception,
    io_name_vocabulary: IONameVocabulary,
) -> ExtractionOutcome:
    content_hash = descriptor.content_hash or capability_content_hash(descriptor)
    evidence = EvidenceRef(
        evidence_type="extraction_error",
        reference=f"capability:{descriptor.capability_id}",
        description="Semantic extraction failed without retaining source or exception text.",
        metadata={"content_hash": content_hash},
    )
    failure = FailureReason(
        code="FINGERPRINT_EXTRACTION_FAILED",
        message="Capability fingerprint extraction failed.",
        evidence=(evidence,),
        details={"exception_type": type(exc).__name__},
    )
    suggestion = ImprovementSuggestion(
        code="CHECK_CAPABILITY_DEFINITION",
        message="Check the capability definition and retry fingerprint extraction.",
        priority=SuggestionPriority.MEDIUM,
        related_failures=(failure.code,),
    )
    fingerprint = CapabilityFingerprint(
        capability_id=descriptor.capability_id,
        capability_type=normalize_capability_type(descriptor.capability_type),
        name=descriptor.name,
        description=descriptor.description,
        source=descriptor.source,
        available=descriptor.available,
        inputs=descriptor.inputs,
        outputs=descriptor.outputs,
        classification=descriptor.classification,
        tags=descriptor.tags,
        content_hash=content_hash,
        semantic_profile=SemanticProfile(
            summary=descriptor.description or descriptor.name,
            keywords=descriptor.tags,
        ),
        failures=(failure,),
        evidence=(evidence,),
        suggestions=(suggestion,),
        metadata=descriptor.metadata,
    )
    return ExtractionOutcome(fingerprint=fingerprint, io_name_vocabulary=io_name_vocabulary)


def _extraction_failure(code: str, message: str) -> FailureReason:
    return FailureReason(code=code, message=message)


__all__ = ["CapabilityFingerprintExtractor", "ExtractionOutcome", "capability_content_hash"]
