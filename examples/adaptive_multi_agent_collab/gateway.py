"""Audited OpenRouter transport registered through openJiuwen's public client API.

The built-in compatible client drops native termination and billing fields. This
text-only client keeps every gateway frame in public message metadata instead of
coercing missing usage into the SDK's default zero-valued UsageMetadata.
"""

from __future__ import annotations

import asyncio
import inspect
import copy
import hashlib
import json
import math
import re
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk

from .identity import validate_model_identity_evidence
from .failure_policy import (
    ACCOUNTING_OBSERVER,
    CALL_TIMING,
    GENERATION_DISPATCH_GATE,
    set_call_phase,
    HARD_FAILURES,
    FailureKind,
    ProviderFailure,
    classify_failure,
    http_failure_kind,
)
from .output_contract import response_format_for, role_response_format, validate_role_output_profile

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_CLIENT = "AdaptiveOpenRouter"
COLLABORATION_MODELS = ("openai/gpt-4.1-mini", "google/gemini-2.5-flash", "anthropic/claude-sonnet-4.6")
DISABLED_PLUGINS = ("web", "file-parser", "response-healing", "context-compression", "pareto-router", "fusion")
SEED_MAPPING_ID = "nonnegative_int32_modulo"
METADATA_LOOKUP_LIMIT = 3
METADATA_LOOKUP_TIMEOUT = 2.0


class _AdmissionTransport(httpx.AsyncBaseTransport):
    """Gate only new generation at HTTPX's final transport handoff.

    HTTPX client setup and request hooks can await after the resource's durable
    acknowledgement. The synchronous gate and transport handoff here have no
    intervening await; already-started POSTs and receipt GETs remain drainable.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self.transport = transport
        self.generation_started = False

    async def __aenter__(self) -> _AdmissionTransport:
        await self.transport.__aenter__()
        return self

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            gate = GENERATION_DISPATCH_GATE.get()
            if gate is not None:
                gate()
            self.generation_started = True
            set_call_phase("generation")
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self.transport.aclose()


@dataclass(frozen=True)
class OpenRouterBinding:
    """Frozen route attestation; this is separate from permission to spend money.

    Evidence must identify the exact provider endpoint and document the transmitted
    capabilities. A service alias without an immutable revision requires an explicit
    limited-reproducibility approval. Constructing a proposal does not approve it.
    """

    model: str
    provider_endpoint: str | None = None
    revision_binding: str | None = None
    binding_evidence: str | None = None
    # Retained only to read existing frozen bindings; not an execution condition.
    account_policy_evidence: str | None = None
    approved: bool = False
    supports_seed: bool = False
    seed_support_evidence: str | None = None
    thinking_disabled_evidence: str | None = None
    immutable_revision: str | None = None
    limited_reproducibility_approved: bool = False
    served_provider: str | None = None
    served_model: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OpenRouterBinding:
        try:
            return cls(**dict(value))
        except TypeError as error:
            raise ValueError("invalid OpenRouter binding fields") from error

    def metadata(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.model not in COLLABORATION_MODELS:
            raise ValueError("the OpenRouter binding must use an exact model from the fixed collaboration pool")
        for flag in (self.approved, self.supports_seed, self.limited_reproducibility_approved):
            if not isinstance(flag, bool):
                raise ValueError("gateway approval and capability flags must be explicit booleans")
        if not self.approved:
            raise ValueError("the exact OpenRouter provider/endpoint/revision binding has not been approved")
        for name in ("provider_endpoint", "revision_binding", "binding_evidence"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"the approved OpenRouter binding requires {name}")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", str(self.provider_endpoint)) is None:
            raise ValueError("provider_endpoint must be the exact documented endpoint slug")
        for name in (
            "immutable_revision",
            "seed_support_evidence",
            "thinking_disabled_evidence",
            "served_provider",
            "served_model",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be nonempty text when supplied")
        if not self.immutable_revision and not self.limited_reproducibility_approved:
            raise ValueError("a service alias requires explicit limited-reproducibility approval")
        if self.supports_seed and not (self.seed_support_evidence or "").strip():
            raise ValueError("seed forwarding requires endpoint-specific support evidence")
        if self.model != COLLABORATION_MODELS[0] and not (self.thinking_disabled_evidence or "").strip():
            raise ValueError("disabling thinking requires endpoint-specific parameter evidence")


def gateway_usage(raw: Any) -> dict[str, Any] | None:
    """Map native totals without estimating costs or inventing missing counts."""

    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("OpenRouter usage must be an object or null")
    result: dict[str, Any] = {}
    for native, normalized in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
        ("cost", "total_cost"),
    ):
        if native in raw:
            result[normalized] = raw[native]
    for source, fields in (
        ("prompt_tokens_details", (("cached_tokens", "cache_tokens"), ("cache_write_tokens", "cache_write_tokens"))),
        ("completion_tokens_details", (("reasoning_tokens", "reasoning_tokens"),)),
    ):
        details = raw.get(source)
        if isinstance(details, Mapping):
            for native, normalized in fields:
                if native in details:
                    result[normalized] = details[native]
    # Keep native detail categories and upstream/provider costs distinct from
    # the actual gateway account charge (usage.cost).
    result["raw_gateway_usage"] = dict(raw)
    result["usage_sources"] = {key: "stream_native" for key in result if key != "raw_gateway_usage"}
    if "input_tokens" in result or "output_tokens" in result:
        result["native_count_source"] = "stream_native"
    if isinstance(raw.get("is_byok"), bool):
        result["is_byok"] = raw["is_byok"]
    return result


def generation_usage(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the generation endpoint's native and normalized counts distinct.

    The generation API's ``tokens_*`` counters use a normalized tokenizer;
    only ``native_tokens_*`` can fill provider-native accounting coordinates.
    ``total_cost`` is the account charge, not ``upstream_inference_cost``.
    """

    result = {
        target: raw[source]
        for source, target in (
            ("native_tokens_prompt", "input_tokens"),
            ("native_tokens_completion", "output_tokens"),
            ("native_tokens_cached", "cache_tokens"),
            ("native_tokens_reasoning", "reasoning_tokens"),
            ("tokens_prompt", "normalized_input_tokens"),
            ("tokens_completion", "normalized_output_tokens"),
            ("total_cost", "total_cost"),
        )
        if raw.get(source) is not None
    }
    result["usage_sources"] = {
        key: "generation_normalized" if key.startswith("normalized_") else "generation_native" for key in result
    }
    result["raw_generation_metadata"] = dict(raw)
    if "input_tokens" in result or "output_tokens" in result:
        result["native_count_source"] = "generation_native"
    if isinstance(raw.get("is_byok"), bool):
        result["is_byok"] = raw["is_byok"]
    return result


async def lookup_generation_metadata(
    generation_id: str,
    api_key: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Perform exactly one bounded metadata GET; the caller journals its intent.

    This never generates text, follows redirects, retries, or includes credentials
    in returned evidence. A caller must stop its audit on HTTP 401 or 403.
    """
    if not isinstance(generation_id, str) or re.fullmatch(r"gen-[A-Za-z0-9_-]+", generation_id) is None:
        raise ValueError("invalid recorded generation identity")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("an OpenRouter API key is required")
    if transport is None:
        transport = httpx.AsyncHTTPTransport(retries=0, verify=True, trust_env=False)
    try:
        async with asyncio.timeout(METADATA_LOOKUP_TIMEOUT):
            async with httpx.AsyncClient(
                transport=transport, timeout=METADATA_LOOKUP_TIMEOUT, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.get(
                    OPENROUTER_BASE_URL + "/generation",
                    params={"id": generation_id},
                    headers={"Authorization": "Bearer " + api_key},
                    timeout=METADATA_LOOKUP_TIMEOUT,
                )
                result: dict[str, Any] = {"http_status": response.status_code}
                if response.status_code == 200:
                    decoded = response.json()
                    payload = decoded.get("data") if isinstance(decoded, dict) else None
                    if not isinstance(payload, dict):
                        raise ValueError("generation metadata data field is not an object")
                    result["data"] = payload
                return result
    except (httpx.HTTPError, TimeoutError, ValueError) as error:
        return {"error_type": type(error).__name__}


def preview_request_body(
    binding: OpenRouterBinding,
    temperature: float,
    messages: Any,
    settings: Mapping[str, Any],
    *,
    role_output: Mapping[str, Any] | None = None,
    response_identity_evidence: Mapping[str, Any] | None = None,
    default_max_tokens: int | None = None,
) -> dict[str, Any]:
    """Use the production serializer without constructing a client or reading credentials."""
    model_config = ModelRequestConfig(model=binding.model, temperature=temperature, max_tokens=default_max_tokens)
    binding.validate()
    validate_role_output_profile(role_output, binding)
    if response_identity_evidence is not None:
        validate_model_identity_evidence(
            binding, response_identity_evidence["metadata_model"], response_identity_evidence
        )
    allowed = {
        "model",
        "temperature",
        "max_tokens",
        "seed",
        "timeout",
        "output_parser",
        "response_format",
        "workflow_call_kind",
    }
    unknown = [key for key, value in settings.items() if value is not None and key not in allowed]
    if unknown:
        raise ValueError(f"unapproved OpenRouter parameters: {sorted(unknown)}")
    if settings.get("model") not in (None, binding.model):
        raise ValueError("a per-call model override cannot change the approved binding")
    if settings.get("output_parser") is not None:
        raise ValueError("parse the complete public Model stream after generation")
    response_format = settings.get("response_format")
    call_kind = settings.get("workflow_call_kind")
    if call_kind not in (None, "role"):
        raise ValueError("the private role marker cannot alter other output protocols")
    if call_kind == "role" and response_format != response_format_for("role", role_output):
        raise ValueError("the frozen role output mode requires its complete response_format without omission")
    if response_format is not None and (
        call_kind != "role"
        or not role_output
        or role_output.get("mode") != "json_schema"
        or response_format != role_response_format()
    ):
        raise ValueError("only the frozen supported strict role schema may be transmitted")
    temperature = settings.get("temperature")
    if temperature is None:
        temperature = model_config.temperature
    if isinstance(temperature, bool) or temperature not in (0.0, 0.7):
        raise ValueError("collaboration temperature is 0.7; TextGrad temperature is 0.0")
    if temperature != model_config.temperature:
        raise ValueError("a per-call temperature override cannot change the frozen model configuration")
    max_tokens = settings.get("max_tokens")
    if max_tokens is None:
        max_tokens = model_config.max_tokens
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("every gateway request requires a positive output token cap")
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    if not isinstance(messages, (list, tuple)) or not messages:
        raise ValueError("gateway messages must be a nonempty text message sequence")
    serialized = []
    for message in messages:
        if isinstance(message, Mapping):
            value = dict(message)
        elif isinstance(message, BaseMessage):
            value = BaseMessage.model_dump(message, exclude_none=True)
        else:
            raise ValueError("gateway messages must be mappings or public SDK message objects")
        if value.get("role") not in ("system", "user", "assistant") or not isinstance(value.get("content"), str):
            raise ValueError("only ordinary text messages are admitted by this experiment")
        # The public SDK carries harmless bookkeeping defaults, but no tool,
        # cache-control, multimodal or provider instructions may be smuggled.
        allowed_message_fields = {"role", "content", "metadata"}
        if any(v not in (None, [], {}) for k, v in value.items() if k not in allowed_message_fields):
            raise ValueError("unsupported message fields in the text-only gateway request")
        serialized.append({"role": value["role"], "content": value["content"]})
    body: dict[str, Any] = {
        "model": binding.model,
        "messages": serialized,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "provider": {
            "only": [binding.provider_endpoint],
            "order": [binding.provider_endpoint],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        # An empty array inherits account defaults, so disable each
        # documented plugin explicitly:
        # https://openrouter.ai/docs/guides/features/plugins
        "plugins": [{"id": name, "enabled": False} for name in DISABLED_PLUGINS],
        "transforms": [],
    }
    if binding.model != COLLABORATION_MODELS[0]:
        # Excluding visible reasoning does not disable its computation.
        body["reasoning"] = {"enabled": False}
    if response_format is not None:
        body["response_format"] = copy.deepcopy(response_format)
    seed = settings.get("seed")
    if seed is not None:
        if not binding.supports_seed:
            raise ValueError("the approved gateway endpoint does not support generation seeds")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("generation seed must be a nonnegative integer")
        # Google GenerationConfig.seed is int32. Use the shared nonnegative
        # range for every seeded route; retain the full logical seed in
        # response metadata rather than changing the workflow identity.
        body["seed"] = seed % (2**31)
    return body


class OpenRouterError(ProviderFailure):
    """A physical gateway attempt failed; generation POSTs are never retried."""

    def __init__(self, message: str, kind: FailureKind = FailureKind.CORRUPTION) -> None:
        super().__init__(message, kind)


class OpenRouterModelClient(BaseModelClient):
    """One generation POST, plus bounded read-only accounting recovery."""

    __client_name__ = OPENROUTER_CLIENT

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig) -> None:
        super().__init__(model_config, model_client_config)
        self.binding = OpenRouterBinding.from_dict(getattr(model_client_config, "openrouter_binding", {}))
        self.binding.validate()
        self.role_output = copy.deepcopy(getattr(model_client_config, "openrouter_role_output", None))
        validate_role_output_profile(self.role_output, self.binding)
        self.response_identity_evidence = copy.deepcopy(
            getattr(model_client_config, "openrouter_response_identity_evidence", None)
        )
        self.response_identity_models = {self.binding.served_model or self.binding.model}
        if self.response_identity_evidence is not None:
            validate_model_identity_evidence(
                self.binding, self.response_identity_evidence["metadata_model"], self.response_identity_evidence
            )
            self.response_identity_models.add(self.response_identity_evidence["metadata_model"])
        if model_client_config.api_base != OPENROUTER_BASE_URL or model_client_config.max_retries != 0:
            raise ValueError("the audited gateway requires the fixed HTTPS base and zero hidden retries")
        if model_client_config.verify_ssl is not True or model_client_config.custom_headers:
            raise ValueError("the audited gateway requires TLS verification and no header overrides")
        if model_config.model_name != self.binding.model:
            raise ValueError("the model configuration and approved gateway binding disagree")
        extras = model_config.model_fields_set - {"model_name", "temperature", "max_tokens"}
        if extras:
            raise ValueError(f"unapproved model decoding configuration: {sorted(extras)}")

    def request_body(self, messages: Any, settings: Mapping[str, Any]) -> dict[str, Any]:
        """Serialize the exact supported experiment request; reject overrides."""
        return preview_request_body(
            self.binding,
            self.model_config.temperature,
            messages,
            settings,
            role_output=self.role_output,
            response_identity_evidence=self.response_identity_evidence,
            default_max_tokens=self.model_config.max_tokens,
        )

    def _chunk(self, raw: dict[str, Any], header_id: str | None, body: dict[str, Any]) -> AssistantMessageChunk:
        choices = raw.get("choices", [])
        protocol_error = None
        if not isinstance(choices, list) or len(choices) > 1 or (choices and not isinstance(choices[0], dict)):
            protocol_error = "the gateway must return at most one ordinary completion choice"
            choices = []
        choice = choices[0] if choices else {}
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            protocol_error = "the gateway returned an invalid completion delta"
            delta = {}
        content = delta.get("content") or ""
        if not isinstance(content, str):
            protocol_error = "the text-only gateway returned nontext content"
            content = ""
        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if reasoning is not None and not isinstance(reasoning, str):
            protocol_error = "the gateway returned nontext reasoning"
            reasoning = None
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            protocol_error = "the gateway returned an invalid finish reason"
            finish_reason = None
        if choice.get("native_finish_reason") is not None and not isinstance(choice["native_finish_reason"], str):
            protocol_error = "the gateway returned an invalid native finish reason"
        try:
            usage = gateway_usage(raw.get("usage"))
        except ValueError:
            protocol_error = "the gateway returned invalid usage metadata"
            usage = None
        metadata = {
            "raw": raw,
            "usage": usage,
            "generation_id": raw.get("id"),
            "header_generation_id": header_id,
            "served_model": raw.get("model"),
            "served_provider": raw.get("provider"),
            "native_finish_reason": choice.get("native_finish_reason"),
            "service_tier": raw.get("service_tier"),
            "system_fingerprint": raw.get("system_fingerprint"),
            "binding": self.binding.metadata(),
            "request_parameters": {key: value for key, value in body.items() if key != "messages"},
            "protocol_error": protocol_error,
        }
        return AssistantMessageChunk(
            content=content,
            reasoning_content=reasoning,
            finish_reason=finish_reason or "null",
            metadata={"openrouter": metadata},
        )

    def _validate_frame(self, chunk: AssistantMessageChunk, identities: dict[str, Any]) -> None:
        metadata = chunk.metadata["openrouter"]
        raw = metadata["raw"]
        if metadata.get("protocol_error"):
            raise OpenRouterError(metadata["protocol_error"])
        for key in ("id", "provider", "model"):
            observed = raw.get(key)
            if observed is not None:
                if key == "model" and observed in self.response_identity_models:
                    observed = self.binding.served_model or self.binding.model
                if key in identities and observed != identities[key]:
                    raise OpenRouterError(
                        f"OpenRouter changed the {key} identity within one generation", FailureKind.BINDING
                    )
                identities[key] = observed
        if raw.get("model") is not None and raw["model"] not in self.response_identity_models:
            raise OpenRouterError("the served model differs from the approved gateway binding", FailureKind.BINDING)
        if self.binding.served_provider and raw.get("provider") not in (None, self.binding.served_provider):
            raise OpenRouterError("the served provider differs from the approved gateway binding", FailureKind.BINDING)
        if raw.get("service_tier") not in (None, "default", "standard"):
            raise OpenRouterError("the gateway used an unrequested non-default service tier", FailureKind.BINDING)
        choice = (raw.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("tool_calls") or delta.get("function_call"):
            raise OpenRouterError("the gateway returned an unrequested tool call", FailureKind.BINDING)
        reasoning_tokens = (metadata.get("usage") or {}).get("reasoning_tokens")
        if (
            chunk.reasoning_content
            or delta.get("reasoning_details")
            or (isinstance(reasoning_tokens, (int, float)) and reasoning_tokens > 0)
        ):
            raise OpenRouterError(
                "the gateway produced reasoning despite the required disabled-thinking mode", FailureKind.BINDING
            )
        if raw.get("error") or chunk.finish_reason == "error":
            error = raw.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            kind = http_failure_kind(code) if isinstance(code, int) else FailureKind.PROVIDER
            raise OpenRouterError("OpenRouter reported a streaming generation error", kind)

    @staticmethod
    def _metadata_missing(known: Mapping[str, Any], identities: Mapping[str, Any]) -> bool:
        required = ("input_tokens", "output_tokens", "total_cost", "cache_tokens", "reasoning_tokens", "is_byok")
        return any(known.get(key) is None for key in required) or any(
            identities.get(key) is None for key in ("id", "provider", "model", "native_finish_reason")
        )

    async def _recover_metadata(
        self,
        client: httpx.AsyncClient,
        body: dict[str, Any],
        identities: dict[str, Any],
        known: dict[str, Any],
        deadline: float,
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Bounded read-only recovery for this ID, never generation replay."""

        generation_id = identities.get("id")
        if not isinstance(generation_id, str) or not generation_id or not self._metadata_missing(known, identities):
            return
        attempts: list[dict[str, Any]] = []
        for index in range(METADATA_LOOKUP_LIMIT):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            record: dict[str, Any] = {"attempt": index + 1, "generation_id": generation_id}
            observer = ACCOUNTING_OBSERVER.get()
            if observer is not None:
                permitted = observer("metadata-lookup-intent", record)
                if inspect.isawaitable(permitted):
                    permitted = await permitted
                if permitted is not True:
                    return
            payload = None
            corrupt_metadata = False
            try:
                # httpx timeouts are per IO operation; the outer timeout also
                # bounds the complete metadata GET within the original call.
                timeout = min(METADATA_LOOKUP_TIMEOUT, remaining)
                async with asyncio.timeout(timeout):
                    response = await client.get(
                        OPENROUTER_BASE_URL + "/generation",
                        params={"id": generation_id},
                        headers={"Authorization": "Bearer " + self.model_client_config.api_key},
                        timeout=timeout,
                    )
                record["http_status"] = response.status_code
                if response.status_code == 200:
                    decoded = response.json()
                    payload = decoded.get("data") if isinstance(decoded, dict) else None
                    if not isinstance(payload, dict):
                        raise ValueError("generation metadata data field is not an object")
                    record["status"] = "received"
                else:
                    record["status"] = "unavailable"
            except (httpx.HTTPError, TimeoutError, ValueError) as error:
                record["status"] = "unavailable"
                record["error_type"] = type(error).__name__
                corrupt_metadata = isinstance(error, ValueError)
            attempts.append(record)
            if observer is not None:
                observed = observer("metadata-lookup-result", record)
                if inspect.isawaitable(observed):
                    await observed
            metadata: dict[str, Any] = {
                "raw": {},
                "usage": None,
                "metadata_lookup_attempts": tuple(dict(item) for item in attempts),
            }
            chunk = AssistantMessageChunk(content="", finish_reason="null", metadata={"openrouter": metadata})
            if payload is not None:
                metadata["raw_generation_metadata"] = payload
                # Never merge another generation's usage into this attempt.
                if payload.get("id") != generation_id:
                    yield chunk
                    raise OpenRouterError("generation metadata returned a different generation id")
                usage = generation_usage(payload)
                metadata.update(
                    usage=usage,
                    generation_id=generation_id,
                    served_model=payload.get("model"),
                    served_provider=payload.get("provider_name"),
                    native_finish_reason=payload.get("native_finish_reason"),
                    service_tier=payload.get("service_tier"),
                    system_fingerprint=payload.get("system_fingerprint"),
                    upstream_id=payload.get("upstream_id"),
                )
                # Validate the same identity/capability rules after exposing
                # this generation's authoritative usage for charge retention.
                metadata["raw"] = {
                    "id": generation_id,
                    "model": payload.get("model"),
                    "provider": payload.get("provider_name"),
                    "service_tier": payload.get("service_tier"),
                }
                reason = payload.get("finish_reason")
                if isinstance(reason, str) and reason:
                    chunk.finish_reason = reason
                yield chunk
                self._validate_frame(chunk, identities)
                known.update({key: value for key, value in usage.items() if value is not None})
                if payload.get("native_finish_reason") is not None:
                    identities["native_finish_reason"] = payload["native_finish_reason"]
            else:
                yield chunk
            if corrupt_metadata:
                raise OpenRouterError("OpenRouter returned malformed generation metadata", FailureKind.CORRUPTION)
            if record.get("http_status") in (401, 403):
                raise OpenRouterError("OpenRouter rejected metadata-recovery credentials", FailureKind.CREDENTIAL)
            if not self._metadata_missing(known, identities):
                return
            # Allow asynchronous accounting to settle, without extending the
            # logical call deadline or creating any extra physical generation.
            if index + 1 < METADATA_LOOKUP_LIMIT:
                await asyncio.sleep(max(0.0, min(0.1, deadline - time.monotonic())))

    async def stream(self, messages: Any, **settings: Any) -> AsyncIterator[AssistantMessageChunk]:
        body = self.request_body(messages, settings)
        timeout = settings.get("timeout") or self.model_client_config.timeout
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("gateway timeout must be finite and positive")
        transport = getattr(self.model_client_config, "openrouter_transport", None)
        if transport is None:
            transport = httpx.AsyncHTTPTransport(retries=0, verify=True, trust_env=False)
        admitted_transport = _AdmissionTransport(transport)
        deadline = time.monotonic() + timeout
        identities: dict[str, Any] = {}
        known: dict[str, Any] = {}
        failure: Exception | None = None
        logical_seed = settings.get("seed")
        identity_evidence = copy.deepcopy(self.response_identity_evidence)
        identity_hash = (
            hashlib.sha256(json.dumps(identity_evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if identity_evidence is not None else None
        )

        async def observe(chunk: AssistantMessageChunk) -> AssistantMessageChunk:
            metadata = chunk.metadata["openrouter"]
            if identity_evidence is not None:
                metadata["response_identity_evidence"] = identity_evidence
                metadata["response_identity_evidence_sha256"] = identity_hash
            metadata["logical_seed"] = logical_seed
            metadata["wire_seed"] = body.get("seed")
            metadata["seed_mapping_id"] = SEED_MAPPING_ID if logical_seed is not None else None
            known.update({key: value for key, value in (metadata.get("usage") or {}).items() if value is not None})
            if metadata.get("native_finish_reason") is not None:
                identities["native_finish_reason"] = metadata["native_finish_reason"]
            observer = ACCOUNTING_OBSERVER.get()
            if observer is not None:
                observed = observer("gateway-frame", metadata)
                if inspect.isawaitable(observed):
                    await observed
            return chunk

        async with httpx.AsyncClient(
            transport=admitted_transport, timeout=timeout, follow_redirects=False, trust_env=False
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    OPENROUTER_BASE_URL + "/chat/completions",
                    headers={
                        "Authorization": "Bearer " + self.model_client_config.api_key,
                        "Accept": "text/event-stream",
                    },
                    json=body,
                ) as response:
                    header_id = response.headers.get("x-generation-id")
                    if header_id:
                        identities["id"] = header_id
                    if response.status_code != 200:
                        payload = await response.aread()
                        try:
                            raw = json.loads(payload)
                        except (ValueError, UnicodeError):
                            raw = {"error": {"message": "non-JSON HTTP error"}}
                        if not isinstance(raw, dict):
                            raw = {"error": raw}
                        raw["http_status"] = response.status_code
                        yield await observe(self._chunk(raw, header_id, body))
                        raise OpenRouterError(
                            f"OpenRouter HTTP {response.status_code}; no retry was dispatched",
                            http_failure_kind(response.status_code),
                        )
                    data: list[str] = []
                    finished = False
                    async for line in response.aiter_lines():
                        if line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip(" "))
                            continue
                        if line or not data:
                            continue
                        event = "\n".join(data)
                        data.clear()
                        if event == "[DONE]":
                            finished = True
                            break
                        try:
                            raw = json.loads(event)
                        except ValueError as error:
                            raise OpenRouterError("malformed OpenRouter SSE data") from error
                        if not isinstance(raw, dict):
                            raise OpenRouterError("OpenRouter SSE data must be a JSON object")
                        chunk = await observe(self._chunk(raw, header_id, body))
                        yield chunk
                        self._validate_frame(chunk, identities)
                    if not finished:
                        raise OpenRouterError(
                            "OpenRouter stream ended before its final [DONE] event", FailureKind.TRANSPORT
                        )
            except Exception as error:
                if not admitted_transport.generation_started:
                    # Admission refusal is not a failed physical generation;
                    # preserve the original pause/halt and perform no lookup.
                    raise
                # A bounded read-only lookup can recover the bill for a failed
                # generation. It never revives partial content or retries POST.
                failure = error
                timing = CALL_TIMING.get()
                if timing is not None and classify_failure(error) in HARD_FAILURES:
                    timing["hard_failure"] = error
            try:
                if failure is None or classify_failure(failure) != FailureKind.CREDENTIAL:
                    set_call_phase(
                        "receipt-recovery", provider_completed=failure is None
                        and identities.get("native_finish_reason") is not None,
                    )
                    async for chunk in self._recover_metadata(client, body, identities, known, deadline):
                        yield await observe(chunk)
            except Exception as error:
                if failure is None or classify_failure(error) in HARD_FAILURES:
                    failure = error
                timing = CALL_TIMING.get()
                if timing is not None and classify_failure(error) in HARD_FAILURES:
                    timing["hard_failure"] = error
            yield AssistantMessageChunk(
                content="",
                finish_reason="null",
                metadata={
                    "openrouter": {
                        "raw": {},
                        "usage": None,
                        "response_identity_verified": failure is None
                        and all(identities.get(key) is not None for key in ("id", "provider", "model")),
                    }
                },
            )
            if failure is not None:
                raise failure

    async def invoke(self, messages: Any, **settings: Any) -> AssistantMessage:
        # All calls use the same lossless stream; even the public invoke surface
        # preserves raw frames rather than using the lossy SDK nonstream parser.
        parser = settings.pop("output_parser", None)
        parts: list[str] = []
        frames = []
        reason = "null"
        async for chunk in self.stream(messages, **settings):
            parts.append(str(chunk.content or ""))
            frames.append(chunk.metadata["openrouter"])
            if chunk.finish_reason != "null":
                reason = chunk.finish_reason
        result = AssistantMessage(content="".join(parts), finish_reason=reason, metadata={"openrouter_frames": frames})
        if parser is not None:
            result.parser_content = await parser.parse(result.content)
        return result

    async def generate_image(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("this experiment permits text generation only")

    async def generate_speech(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("this experiment permits text generation only")

    async def generate_video(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("this experiment permits text generation only")


def build_openrouter_model(
    binding: OpenRouterBinding,
    api_key: str,
    *,
    temperature: float,
    transport: httpx.AsyncBaseTransport | None = None,
    response_identity_evidence: Mapping[str, Any] | None = None,
    role_output: Mapping[str, Any] | None = None,
) -> Model:
    """Build the public Model locally; validation never probes an external API."""

    binding.validate()
    validate_role_output_profile(role_output, binding)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("an OpenRouter API key is required")
    if isinstance(temperature, bool) or temperature not in (0.0, 0.7):
        raise ValueError("the approved temperatures are collaboration 0.7 and TextGrad 0.0")
    config: dict[str, Any] = {
        "client_provider": OPENROUTER_CLIENT,
        "api_base": OPENROUTER_BASE_URL,
        "api_key": api_key,
        "max_retries": 0,
        "verify_ssl": True,
        "timeout": 120.0,
        "stream_first_chunk_timeout": 120.0,
        "stream_idle_timeout": 120.0,
        "openrouter_binding": binding.metadata(),
    }
    if transport is not None:
        config["openrouter_transport"] = transport
    if role_output is not None:
        config["openrouter_role_output"] = copy.deepcopy(dict(role_output))
    if response_identity_evidence is not None:
        validate_model_identity_evidence(
            binding, response_identity_evidence["metadata_model"], response_identity_evidence
        )
        config["openrouter_response_identity_evidence"] = copy.deepcopy(dict(response_identity_evidence))
    return Model(
        model_client_config=ModelClientConfig(**config),
        model_config=ModelRequestConfig(model=binding.model, temperature=temperature),
    )
