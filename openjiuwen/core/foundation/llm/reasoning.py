# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve provider-neutral reasoning config into provider wire parameters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.reasoning_profiles import (
    MODEL_REASONING_FALLBACKS,
    REASONING_PROFILES,
    ReasoningCapability,
    ReasoningProfile,
    find_reasoning_profile,
    profile_reasoning_capability,
    standard_reasoning_capability,
)
from openjiuwen.core.foundation.llm.schema.config import (
    LLMApiMode,
    LLMAuthMode,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
    ReasoningConfig,
)


UNSET_REASONING = object()

_NEW_REASONING_KEYS = {"mode", "effort", "budget_tokens"}
_LEGACY_REASONING_TOP_LEVEL_FIELDS = {
    "reasoning_effort",
    "thinking",
    "enable_thinking",
    "thinking_budget",
    "thinking_strategy",
    "chat_template_kwargs",
}
_LEGACY_REASONING_EXTRA_BODY_FIELDS = {
    "reasoning",
    "reasoning_effort",
    "thinking",
    "enable_thinking",
    "thinking_budget",
    "thinking_strategy",
}
_REASONING_WIRE_KEYS = (
    "reasoning",
    "reasoning_effort",
    "thinking",
    "enable_thinking",
    "thinking_budget",
    "thinking_strategy",
    "output_config",
)

_DISABLED_REASONING_EFFORTS = {"off", "none"}
_GENERIC_ENDPOINT_PROFILES = {"", "openai", "openai-compatible", "openai_compatible"}
_API_BASE_PROVIDER_HINTS: tuple[tuple[str, str], ...] = (
    ("openrouter.ai", "openrouter"),
    ("dashscope.aliyuncs.com", "dashscope"),
    ("dashscope-intl.aliyuncs.com", "dashscope"),
    ("maas.aliyuncs.com", "dashscope"),
    ("api.deepseek.com", "deepseek"),
    ("api.moonshot.cn", "moonshot"),
    ("api.moonshot.ai", "moonshot"),
    ("api.kimi.com", "moonshot"),
    ("api.minimax.io", "minimax"),
    ("api.minimaxi.com", "minimax"),
    ("api.modelarts-maas.com", "modelarts"),
    ("api-ap-southeast-1.modelarts-maas.com", "modelarts"),
    ("qianfan.baidubce.com", "qianfan"),
    ("open.bigmodel.cn", "zhipu"),
    ("api.z.ai", "zhipu"),
    ("ark.cn-beijing.volces.com", "volcengine"),
    ("ark.volces.com", "volcengine"),
    ("api.xiaomimimo.com", "mimo"),
    ("token-plan-cn.xiaomimimo.com", "mimo"),
    ("token-plan-sgp.xiaomimimo.com", "mimo"),
    ("token-plan-ams.xiaomimimo.com", "mimo"),
)


@dataclass(frozen=True)
class ReasoningPlan:
    sdk_params: dict[str, Any] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def provider_identity(client_config: ModelClientConfig) -> str:
    provider = getattr(client_config, "legacy_client_provider", None) or getattr(client_config, "client_provider", "")
    return str(_value(provider) or "").strip()


def _provider_key(client_config: ModelClientConfig) -> str:
    return _provider_key_from_values(
        provider=provider_identity(client_config),
        endpoint_profile=getattr(client_config, "endpoint_profile", ""),
        api_base=getattr(client_config, "api_base", ""),
    )


def _provider_key_from_values(*, provider: Any, endpoint_profile: Any, api_base: Any) -> str:
    profile = str(endpoint_profile or "").strip().lower().replace("_", "-")
    provider_key = str(_value(provider) or "").strip().lower().replace("_", "-")
    if profile not in _GENERIC_ENDPOINT_PROFILES:
        return profile
    inferred = _provider_from_api_base(api_base)
    return inferred or profile or provider_key


def _provider_from_api_base(api_base: Any) -> str:
    raw = str(api_base or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.split("@")[-1].split(":", 1)[0]
    for suffix, provider in _API_BASE_PROVIDER_HINTS:
        if host == suffix or host.endswith(f".{suffix}"):
            return provider
    return ""


def resolve_reasoning_protocol(client_config: ModelClientConfig) -> str:
    api_mode = _value(getattr(client_config, "api_mode", None))
    auth_mode = _value(getattr(client_config, "auth_mode", None))
    provider = provider_identity(client_config).strip().lower().replace("_", "")

    if api_mode == LLMApiMode.AnthropicMessages.value or provider == ProviderType.Anthropic.value.lower():
        return LLMApiMode.AnthropicMessages.value
    if (
        api_mode == LLMApiMode.Responses.value
        or auth_mode == LLMAuthMode.OpenAIAccountOAuth.value
        or provider == ProviderType.OpenAIAccount.value.lower()
    ):
        return LLMApiMode.Responses.value
    return LLMApiMode.ChatCompletions.value


def get_reasoning_capability(
    *,
    provider: Any,
    model: str,
    protocol: Any = LLMApiMode.ChatCompletions.value,
    api_base: str = "",
    endpoint_profile: Any = None,
) -> ReasoningCapability:
    """Resolve UI-safe reasoning controls without constructing a model client.

    The lookup is advisory. Listed provider/model exceptions return exact
    capabilities; unlisted models inherit the standard selected protocol.
    """

    protocol_key = str(_value(protocol) or "").strip().lower()
    if protocol_key in {"anthropic", "messages", "anthropic-messages"}:
        protocol_key = LLMApiMode.AnthropicMessages.value
    elif protocol_key in {"openai", "chat", "chat-completions"}:
        protocol_key = LLMApiMode.ChatCompletions.value
    provider_key = _provider_key_from_values(
        provider=provider,
        endpoint_profile=endpoint_profile,
        api_base=api_base,
    )
    profile = find_reasoning_profile(provider_key, model)
    if profile is not None:
        return profile_reasoning_capability(profile, protocol_key)
    return standard_reasoning_capability(protocol_key)


def get_provider_reasoning_rules(
    *,
    provider: Any,
    api_base: str = "",
    endpoint_profile: Any = None,
) -> list[dict[str, Any]]:
    """Return provider-scoped pattern rules for frontend capability matching.

    Order matters: callers must apply the first matching rule, mirroring
    ``find_reasoning_profile``. Models not matched by any rule should fall
    back to the cross-provider model fallbacks and protocol defaults from
    ``get_reasoning_capability_catalog()``.
    """

    provider_key = _provider_key_from_values(
        provider=provider,
        endpoint_profile=endpoint_profile,
        api_base=api_base,
    )
    return [
        {
            "patterns": list(profile.model_patterns),
            "capabilities": {
                "openai": profile_reasoning_capability(
                    profile,
                    LLMApiMode.ChatCompletions.value,
                ).to_dict(),
                "anthropic": profile_reasoning_capability(
                    profile,
                    LLMApiMode.AnthropicMessages.value,
                ).to_dict(),
            },
        }
        for profile in REASONING_PROFILES
        if profile.provider == provider_key
    ]


def get_reasoning_capability_catalog() -> dict[str, Any]:
    """Return the compact frontend catalog for custom-model resolution."""

    return {
        "protocol_defaults": {
            "openai": standard_reasoning_capability(LLMApiMode.ChatCompletions.value).to_dict(),
            "anthropic": standard_reasoning_capability(LLMApiMode.AnthropicMessages.value).to_dict(),
        },
        "model_fallbacks": [
            {
                "patterns": list(profile.model_patterns),
                "capabilities": {
                    "openai": profile_reasoning_capability(
                        profile,
                        LLMApiMode.ChatCompletions.value,
                    ).to_dict(),
                    "anthropic": profile_reasoning_capability(
                        profile,
                        LLMApiMode.AnthropicMessages.value,
                    ).to_dict(),
                },
            }
            for profile in MODEL_REASONING_FALLBACKS
        ],
    }


def _model_name(model_config: ModelRequestConfig, request_model: Optional[str]) -> str:
    return str(request_model or getattr(model_config, "model_name", "") or "").strip()


def _as_plain_mapping(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    return dict(value)


def _coerce_reasoning_config(value: Any) -> tuple[Optional[ReasoningConfig], Optional[dict[str, Any]]]:
    if value is None or value is UNSET_REASONING:
        return None, None
    if isinstance(value, ReasoningConfig):
        return value, None
    raw = _as_plain_mapping(value)
    if raw is None:
        return None, None
    if set(raw).issubset(_NEW_REASONING_KEYS):
        return ReasoningConfig(**raw), None
    return None, raw


def is_reasoning_config_intent(value: Any) -> bool:
    reasoning, _ = _coerce_reasoning_config(value)
    return reasoning is not None


def _model_config_extras(model_config: ModelRequestConfig) -> dict[str, Any]:
    return model_config.model_dump(
        exclude={"model_name", "model", "temperature", "top_p", "max_tokens", "stop", "reasoning"},
        exclude_none=True,
    )


def _merge_for_legacy_detection(
    model_config: ModelRequestConfig,
    explicit_kwargs: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = _model_config_extras(model_config)
    if explicit_kwargs:
        merged.update(dict(explicit_kwargs))
    return merged


def _has_legacy_reasoning_controls(values: Mapping[str, Any]) -> bool:
    for key in _LEGACY_REASONING_TOP_LEVEL_FIELDS:
        if key in values and values[key] is not None:
            return True
    extra_body = values.get("extra_body")
    if isinstance(extra_body, Mapping):
        return any(key in extra_body and extra_body[key] is not None for key in _LEGACY_REASONING_EXTRA_BODY_FIELDS)
    return False


def _explicit_reasoning_value(
    model_config: ModelRequestConfig,
    explicit_kwargs: Mapping[str, Any] | None,
) -> Any:
    if explicit_kwargs and "reasoning" in explicit_kwargs:
        return explicit_kwargs["reasoning"]
    return getattr(model_config, "reasoning", None)


def resolve_reasoning_plan(
    client_config: ModelClientConfig,
    model_config: ModelRequestConfig,
    request_model: Optional[str] = None,
    explicit_kwargs: Mapping[str, Any] | None = None,
) -> ReasoningPlan:
    explicit_kwargs = explicit_kwargs or {}
    protocol = resolve_reasoning_protocol(client_config)
    model = _model_name(model_config, request_model)
    provider = _provider_key(client_config)
    current_values = _merge_for_legacy_detection(model_config, explicit_kwargs)
    reasoning_value = _explicit_reasoning_value(model_config, explicit_kwargs)
    has_explicit_reasoning = "reasoning" in explicit_kwargs
    reasoning, raw_reasoning = _coerce_reasoning_config(reasoning_value)

    if raw_reasoning is not None:
        if protocol in {LLMApiMode.ChatCompletions.value, LLMApiMode.Responses.value}:
            return ReasoningPlan(sdk_params={"reasoning": raw_reasoning})
        return ReasoningPlan()

    if reasoning is None:
        return ReasoningPlan()

    legacy_values = dict(explicit_kwargs) if has_explicit_reasoning else current_values
    if _has_legacy_reasoning_controls(legacy_values):
        return ReasoningPlan(
            warnings=["reasoning config ignored because explicit legacy reasoning controls are already set"]
        )

    if protocol == LLMApiMode.AnthropicMessages.value:
        return _anthropic_reasoning_plan(
            reasoning,
            client_config=client_config,
            model=model,
        )
    if protocol == LLMApiMode.Responses.value:
        return _openai_responses_reasoning_plan(reasoning)
    return _openai_chat_reasoning_plan(
        provider=provider,
        model=model,
        reasoning=reasoning,
        client_config=client_config,
    )


def _is_deepseek(client_config: ModelClientConfig) -> bool:
    if _provider_key(client_config) == "deepseek":
        return True
    identity = provider_identity(client_config).strip().lower().replace("_", "-")
    if identity == "deepseek":
        return True
    api_base = str(getattr(client_config, "api_base", "") or "").lower()
    return "deepseek.com" in api_base


def _anthropic_reasoning_plan(
    reasoning: ReasoningConfig,
    *,
    client_config: ModelClientConfig,
    model: str,
) -> ReasoningPlan:
    provider = _provider_key(client_config)
    if provider == "vllm":
        return _vllm_enable_thinking_plan(reasoning)
    profile = find_reasoning_profile(provider, model)
    if profile is not None:
        return _profile_reasoning_plan(
            profile,
            protocol=LLMApiMode.AnthropicMessages.value,
            model=model,
            reasoning=reasoning,
        )
    return _standard_anthropic_reasoning_plan(reasoning)


def _standard_anthropic_reasoning_plan(reasoning: ReasoningConfig) -> ReasoningPlan:
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        return ReasoningPlan(sdk_params={"thinking": {"type": "disabled"}})

    has_effort = bool(reasoning.effort)
    has_budget = reasoning.budget_tokens is not None
    if reasoning.mode == "auto" and not has_effort and not has_budget:
        return ReasoningPlan()

    thinking: dict[str, Any] = {"type": "enabled" if has_budget else "adaptive"}
    if has_budget:
        thinking["budget_tokens"] = reasoning.budget_tokens
    sdk_params: dict[str, Any] = {"thinking": thinking}
    if has_effort:
        sdk_params["output_config"] = {"effort": reasoning.effort}
    return ReasoningPlan(sdk_params=sdk_params)


def _openai_responses_reasoning_plan(reasoning: ReasoningConfig) -> ReasoningPlan:
    if reasoning.mode == "disabled":
        return ReasoningPlan(sdk_params={"reasoning": None})
    payload: dict[str, Any] = {}
    if reasoning.effort:
        payload["effort"] = reasoning.effort
    return ReasoningPlan(sdk_params={"reasoning": payload}) if payload else ReasoningPlan()


def _openai_chat_reasoning_plan(
    provider: str,
    model: str,
    reasoning: ReasoningConfig,
    client_config: ModelClientConfig | None = None,
) -> ReasoningPlan:
    if provider == "vllm":
        # Capability still comes from the model-name fallback table; only the
        # wire is forced. Official GLM/DeepSeek thinking.type is ignored here.
        return _vllm_enable_thinking_plan(reasoning)
    if client_config is not None and _is_deepseek(client_config):
        provider = "deepseek"
    profile = find_reasoning_profile(provider, model)
    if profile is not None:
        return _profile_reasoning_plan(
            profile,
            protocol=LLMApiMode.ChatCompletions.value,
            model=model,
            reasoning=reasoning,
        )
    return _reasoning_effort_plan(reasoning, disabled_value="none")


def _profile_reasoning_plan(
    profile: ReasoningProfile,
    *,
    protocol: str,
    model: str,
    reasoning: ReasoningConfig,
) -> ReasoningPlan:
    """Encode one declarative profile using a small set of wire formats."""

    wire = (
        profile.anthropic_wire
        if protocol == LLMApiMode.AnthropicMessages.value
        else profile.openai_wire
    )
    model_key = model.lower()
    capability = profile_reasoning_capability(profile, protocol)

    disabled = reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort)
    if wire == "minimax_m2":
        return ReasoningPlan(extra_body={"reasoning_split": True})
    if disabled and "off" not in capability.options:
        return ReasoningPlan()
    if wire in {"unsupported", "always_on"}:
        return ReasoningPlan()
    if wire == "standard_openai":
        return _reasoning_effort_plan(reasoning, disabled_value="none")
    if wire == "standard_anthropic":
        return _standard_anthropic_reasoning_plan(reasoning)
    if wire == "anthropic_manual":
        return _anthropic_manual_budget_plan(reasoning)
    if wire == "openrouter":
        return _openrouter_reasoning_plan(model_key, reasoning)
    if wire == "qwen38":
        return _dashscope_qwen38_reasoning_plan(reasoning)
    if wire == "enable_thinking" or wire == "qianfan_toggle":
        return _qwen_enable_thinking_plan(reasoning)
    if wire == "dashscope_budget":
        return _dashscope_anthropic_reasoning_plan(reasoning)
    if wire == "qwen38_anthropic":
        return _anthropic_output_config_effort_plan(reasoning, _normalize_qwen38_effort)
    if wire == "dashscope_glm_anthropic":
        return _anthropic_output_config_effort_plan(reasoning, _normalize_glm_effort)
    if wire == "thinking_toggle":
        return _thinking_type_toggle_plan(reasoning)
    if wire == "anthropic_toggle":
        return _anthropic_thinking_toggle_plan(reasoning)
    if wire == "deepseek":
        return _deepseek_chat_reasoning_plan(reasoning, model_key)
    if wire == "deepseek_anthropic":
        return _deepseek_anthropic_reasoning_plan(reasoning, model_key)
    if wire == "modelarts_deepseek":
        return _thinking_type_with_effort_plan(
            reasoning,
            effort_in_extra_body=True,
            effort_normalizer=lambda effort: "high" if effort else None,
        )
    if wire == "modelarts_deepseek_anthropic":
        return _anthropic_thinking_with_extra_effort_plan(
            reasoning,
            lambda effort: "high" if effort else None,
        )
    if wire == "glm":
        return _thinking_type_with_effort_plan(
            reasoning,
            effort_in_extra_body=True,
            effort_normalizer=_normalize_glm_effort,
        )
    if wire == "glm_anthropic":
        return _anthropic_thinking_with_extra_effort_plan(
            reasoning,
            _normalize_glm_effort,
        )
    if wire == "kimi_k3":
        if protocol == LLMApiMode.AnthropicMessages.value:
            return _moonshot_anthropic_reasoning_plan(model, reasoning)
        return _moonshot_chat_reasoning_plan(model, reasoning)
    if wire == "minimax_m3":
        return _minimax_chat_reasoning_plan(model_key, reasoning)
    if wire == "minimax_m3_anthropic":
        return _minimax_anthropic_reasoning_plan(model, reasoning)
    if wire == "volcengine_seed":
        effort = _normalize_volcengine_seed_effort(reasoning.effort)
        if reasoning.mode == "disabled":
            return ReasoningPlan(sdk_params={"reasoning_effort": "minimal"})
        if effort:
            return ReasoningPlan(sdk_params={"reasoning_effort": effort})
        return ReasoningPlan()
    return ReasoningPlan()


def _deepseek_chat_reasoning_plan(reasoning: ReasoningConfig, model_key: str = "") -> ReasoningPlan:
    extra_body: dict[str, Any] = {}
    sdk_params: dict[str, Any] = {}
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        extra_body["thinking"] = {"type": "disabled"}
        return ReasoningPlan(extra_body=extra_body)
    effort = _normalize_deepseek_effort(reasoning.effort, model_key)
    if reasoning.mode == "enabled":
        extra_body["thinking"] = {"type": "enabled"}
    if effort:
        sdk_params["reasoning_effort"] = effort
    return ReasoningPlan(sdk_params=sdk_params, extra_body=extra_body)


def _deepseek_anthropic_reasoning_plan(reasoning: ReasoningConfig, model_key: str = "") -> ReasoningPlan:
    sdk_params: dict[str, Any] = {}
    effort = _normalize_deepseek_effort(reasoning.effort, model_key)
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        sdk_params["thinking"] = {"type": "disabled"}
    elif effort:
        sdk_params["thinking"] = {"type": "enabled"}
        sdk_params["output_config"] = {"effort": effort}
    elif reasoning.mode == "enabled":
        sdk_params["thinking"] = {"type": "enabled"}
    return ReasoningPlan(sdk_params=sdk_params)


def _normalize_deepseek_effort(effort: Any, model_key: str = "") -> str | None:
    if not isinstance(effort, str):
        return None
    value = effort.strip().lower()
    if value == "minimal":
        return "low"
    if value == "medium":
        return "high"
    if value == "xhigh":
        return "max" if "pro" in model_key else "high"
    if value in {"low", "high", "max"}:
        return value
    return None


def _normalize_glm_effort(effort: Any) -> str | None:
    if not isinstance(effort, str):
        return None
    value = effort.strip().lower()
    if value in {"none", "off", "minimal"}:
        return "none"
    if value in {"low", "medium", "high"}:
        return "high"
    if value in {"xhigh", "max"}:
        return "max"
    return None


def _normalize_volcengine_seed_effort(effort: Any) -> str | None:
    if not isinstance(effort, str):
        return None
    value = effort.strip().lower()
    if value in {"none", "off", "minimal"}:
        return "minimal"
    if value in {"low", "medium", "high"}:
        return value
    if value in {"xhigh", "max"}:
        return "high"
    return None


def _moonshot_model_family(model: str) -> str:
    model_key = model.lower().rsplit("/", 1)[-1]
    if model_key.startswith("kimi-k3"):
        return "k3"
    if "k2.7-code" in model_key:
        return "k27_code"
    if model_key.startswith("kimi-k2.6") or model_key.startswith("kimi-k2.5"):
        return "k2_toggle"
    return "unknown"


def _moonshot_chat_reasoning_plan(model: str, reasoning: ReasoningConfig) -> ReasoningPlan:
    family = _moonshot_model_family(model)
    if family == "k3":
        if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
            return ReasoningPlan()
        effort = _normalize_kimi_k3_effort(reasoning.effort)
        if effort:
            return ReasoningPlan(sdk_params={"reasoning_effort": effort})
        return ReasoningPlan()
    if family == "k27_code":
        return ReasoningPlan()
    if family != "k2_toggle":
        return ReasoningPlan()
    extra_body: dict[str, Any] = {}
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        extra_body["thinking"] = {"type": "disabled"}
    elif reasoning.mode == "enabled":
        extra_body["thinking"] = {"type": "enabled"}
    return ReasoningPlan(extra_body=extra_body)


def _moonshot_anthropic_reasoning_plan(model: str, reasoning: ReasoningConfig) -> ReasoningPlan:
    family = _moonshot_model_family(model)
    if family == "k3":
        if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
            return ReasoningPlan()
        effort = _normalize_kimi_k3_effort(reasoning.effort)
        if effort:
            return ReasoningPlan(extra_body={"reasoning_effort": effort})
        return ReasoningPlan()
    if family == "k27_code":
        return ReasoningPlan()
    if family != "k2_toggle":
        return ReasoningPlan()
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        return ReasoningPlan(sdk_params={"thinking": {"type": "disabled"}})
    if reasoning.mode == "auto":
        return ReasoningPlan()
    return ReasoningPlan(sdk_params={"thinking": {"type": "enabled"}})


def _normalize_kimi_k3_effort(effort: Any) -> str | None:
    if not isinstance(effort, str):
        return None
    value = effort.strip().lower()
    if value in {"ultra", "xhigh", "max"}:
        return "max"
    if value in {"medium", "high"}:
        return "high"
    if value in {"minimum", "minimal", "light", "low"}:
        return "low"
    return None


def _dashscope_anthropic_reasoning_plan(reasoning: ReasoningConfig) -> ReasoningPlan:
    """Anthropic-compatible toggle for DashScope models without an effort field.

    budget_tokens stays optional on this endpoint (the vendor default is the
    model's maximum chain-of-thought length), so plain "enabled" deliberately
    sends no budget instead of pinning the old 1024 API minimum, which crippled
    thinking depth.
    """
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        return ReasoningPlan(sdk_params={"thinking": {"type": "disabled"}})
    if reasoning.mode == "auto" and not reasoning.effort and reasoning.budget_tokens is None:
        return ReasoningPlan()
    thinking: dict[str, Any] = {"type": "enabled"}
    if reasoning.budget_tokens is not None:
        thinking["budget_tokens"] = reasoning.budget_tokens
    return ReasoningPlan(sdk_params={"thinking": thinking})


def _anthropic_output_config_effort_plan(
    reasoning: ReasoningConfig,
    effort_normalizer,
) -> ReasoningPlan:
    """Anthropic-compatible thinking with vendor-native output_config.effort.

    Used by Model Studio (DashScope) Anthropic endpoints where budget_tokens is
    deprecated and effort is the documented intensity control. An explicit
    numeric budget still wins (sent alone: DashScope rejects budget + effort
    together); plain "enabled" sends neither and uses the vendor default.
    """
    effort = effort_normalizer(reasoning.effort)
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort) or effort == "none":
        return ReasoningPlan(sdk_params={"thinking": {"type": "disabled"}})
    if reasoning.mode == "auto" and not reasoning.effort and reasoning.budget_tokens is None:
        return ReasoningPlan()
    thinking: dict[str, Any] = {"type": "enabled"}
    sdk_params: dict[str, Any] = {"thinking": thinking}
    if reasoning.budget_tokens is not None:
        thinking["budget_tokens"] = reasoning.budget_tokens
    elif effort:
        sdk_params["output_config"] = {"effort": effort}
    return ReasoningPlan(sdk_params=sdk_params)


# Labeled effort -> thinking.budget_tokens for Anthropic endpoints that only
# support manual budget-based thinking (no effort field). Values follow the
# official guidance (minimum 1024; ~1-2k for simple tasks, 16k+ for complex)
# and match pi's defaults; xhigh/max clamp to high like pi does on budget-only
# models.
_ANTHROPIC_MANUAL_BUDGET_BY_EFFORT = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 16384,
    "max": 16384,
}


def _anthropic_manual_budget_plan(reasoning: ReasoningConfig) -> ReasoningPlan:
    """Budget-only Anthropic thinking (Claude Sonnet 4.5 and earlier).

    The API requires budget_tokens >= 1024 whenever thinking is enabled, so a
    labeled effort maps onto a budget and plain "enabled" uses the "medium"
    budget (the capability's recommended level) instead of the old 1024
    minimum, which encoded the weakest possible thinking.
    """
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        return ReasoningPlan(sdk_params={"thinking": {"type": "disabled"}})
    if reasoning.mode == "auto" and not reasoning.effort and reasoning.budget_tokens is None:
        return ReasoningPlan()
    if reasoning.budget_tokens is not None:
        budget = reasoning.budget_tokens
    else:
        effort = str(reasoning.effort or "").strip().lower()
        budget = _ANTHROPIC_MANUAL_BUDGET_BY_EFFORT.get(effort, 8192)
    return ReasoningPlan(
        sdk_params={"thinking": {"type": "enabled", "budget_tokens": budget}}
    )


def _minimax_chat_reasoning_plan(model_key: str, reasoning: ReasoningConfig) -> ReasoningPlan:
    if model_key.startswith("minimax-m3"):
        return _thinking_type_toggle_plan(reasoning, enabled_type="adaptive")
    if model_key.startswith("minimax-m2"):
        return ReasoningPlan(extra_body={"reasoning_split": True})
    return ReasoningPlan()


def _minimax_anthropic_reasoning_plan(model: str, reasoning: ReasoningConfig) -> ReasoningPlan:
    model_key = model.lower()
    if model_key.startswith("minimax-m3"):
        if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
            return ReasoningPlan(sdk_params={"thinking": {"type": "disabled"}})
        if reasoning.mode == "enabled":
            return ReasoningPlan(sdk_params={"thinking": {"type": "adaptive"}})
    if model_key.startswith("minimax-m2"):
        return ReasoningPlan()
    return ReasoningPlan()


# DashScope-compatible thinking_budget for labeled UI efforts. Self-hosted
# DashScope-style gateways (probed 2026-08-28: vLLM serving GLM/DeepSeek/Qwen)
# honor enable_thinking and ignore official thinking.type /
# reasoning_effort=none; intensity is therefore encoded the same way
# DashScope Qwen does, via thinking_budget. Callers opt in with
# endpoint_profile="vllm"; no api_base host is hardcoded here.
_VLLM_DASHSCOPE_BUDGET_BY_EFFORT = {
    "minimal": 1024,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
    "max": 32768,
}


def _vllm_enable_thinking_plan(reasoning: ReasoningConfig) -> ReasoningPlan:
    """DashScope-style switch + intensity for self-hosted OpenAI-compatible gateways.

    Switch: enable_thinking and chat_template_kwargs.enable_thinking (both
    independently disable thinking on the probed gateway). Intensity: explicit
    budget_tokens wins; otherwise a labeled effort maps to thinking_budget.
    reasoning_effort is deliberately NOT sent: the probed gateway ignores it,
    and official DashScope rejects thinking_budget + reasoning_effort together.
    """
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        return ReasoningPlan(
            extra_body={
                "enable_thinking": False,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        )
    if reasoning.mode == "auto" and not reasoning.effort and reasoning.budget_tokens is None:
        return ReasoningPlan()

    extra_body: dict[str, Any] = {
        "enable_thinking": True,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    if reasoning.budget_tokens is not None:
        extra_body["thinking_budget"] = reasoning.budget_tokens
    else:
        effort = str(reasoning.effort or "").strip().lower()
        budget = _VLLM_DASHSCOPE_BUDGET_BY_EFFORT.get(effort)
        if budget is not None:
            extra_body["thinking_budget"] = budget
    return ReasoningPlan(extra_body=extra_body)


def _qwen_enable_thinking_plan(reasoning: ReasoningConfig) -> ReasoningPlan:
    extra_body: dict[str, Any] = {}
    if reasoning.mode == "enabled" and not _is_disabled_reasoning_effort(reasoning.effort):
        extra_body["enable_thinking"] = True
    elif reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        extra_body["enable_thinking"] = False
    if reasoning.budget_tokens is not None:
        extra_body["thinking_budget"] = reasoning.budget_tokens
    return ReasoningPlan(extra_body=extra_body)


def _dashscope_qwen38_reasoning_plan(reasoning: ReasoningConfig) -> ReasoningPlan:
    extra_body: dict[str, Any] = {}
    effort = _normalize_qwen38_effort(reasoning.effort)
    if reasoning.mode == "disabled" or effort == "none":
        return ReasoningPlan(extra_body={"enable_thinking": False})
    if reasoning.mode == "enabled":
        extra_body["enable_thinking"] = True
    if reasoning.budget_tokens is not None:
        extra_body["thinking_budget"] = reasoning.budget_tokens
    elif effort:
        extra_body["reasoning_effort"] = effort
    return ReasoningPlan(extra_body=extra_body)


def _normalize_qwen38_effort(effort: Any) -> str | None:
    if not isinstance(effort, str):
        return None
    value = effort.strip().lower()
    if value in {"none", "off"}:
        return "none"
    if value in {"minimal", "low"}:
        return "low"
    if value == "medium":
        return "medium"
    if value in {"high", "xhigh", "max"}:
        return "xhigh"
    return None


def _thinking_type_toggle_plan(
    reasoning: ReasoningConfig,
    *,
    enabled_type: str = "enabled",
) -> ReasoningPlan:
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        return ReasoningPlan(extra_body={"thinking": {"type": "disabled"}})
    if reasoning.mode == "enabled":
        return ReasoningPlan(extra_body={"thinking": {"type": enabled_type}})
    return ReasoningPlan()


def _thinking_type_with_effort_plan(
    reasoning: ReasoningConfig,
    *,
    effort_in_extra_body: bool,
    effort_normalizer=_normalize_deepseek_effort,
) -> ReasoningPlan:
    effort = effort_normalizer(reasoning.effort)
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort) or effort == "none":
        return ReasoningPlan(extra_body={"thinking": {"type": "disabled"}})
    plan = _thinking_type_toggle_plan(reasoning)
    extra_body = dict(plan.extra_body)
    sdk_params = dict(plan.sdk_params)
    if effort:
        target = extra_body if effort_in_extra_body else sdk_params
        target["reasoning_effort"] = effort
    return ReasoningPlan(sdk_params=sdk_params, extra_body=extra_body)


def _anthropic_thinking_toggle_plan(reasoning: ReasoningConfig) -> ReasoningPlan:
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        return ReasoningPlan(sdk_params={"thinking": {"type": "disabled"}})
    if reasoning.mode == "enabled":
        return ReasoningPlan(sdk_params={"thinking": {"type": "enabled"}})
    return ReasoningPlan()


def _anthropic_thinking_with_extra_effort_plan(
    reasoning: ReasoningConfig,
    effort_normalizer,
) -> ReasoningPlan:
    effort = effort_normalizer(reasoning.effort)
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort) or effort == "none":
        return ReasoningPlan(sdk_params={"thinking": {"type": "disabled"}})
    plan = _anthropic_thinking_toggle_plan(reasoning)
    sdk_params = dict(plan.sdk_params)
    extra_body: dict[str, Any] = {}
    if effort:
        extra_body["reasoning_effort"] = effort
    return ReasoningPlan(sdk_params=sdk_params, extra_body=extra_body)


def _openrouter_reasoning_plan(model_key: str, reasoning: ReasoningConfig) -> ReasoningPlan:
    provider_prefix = model_key.split("/", 1)[0] if "/" in model_key else ""
    known_reasoning_prefixes = {
        "anthropic",
        "deepseek",
        "google",
        "minimax",
        "moonshotai",
        "openai",
        "poolside",
        "qwen",
        "z-ai",
    }
    if provider_prefix and provider_prefix not in known_reasoning_prefixes:
        return ReasoningPlan()

    payload: dict[str, Any] = {}
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        payload["effort"] = "none"
    elif reasoning.mode == "enabled":
        payload["enabled"] = True
    if reasoning.budget_tokens is not None:
        payload["max_tokens"] = reasoning.budget_tokens
    elif reasoning.effort and payload.get("effort") != "none":
        payload["effort"] = reasoning.effort
    return ReasoningPlan(extra_body={"reasoning": payload}) if payload else ReasoningPlan()


def _reasoning_effort_plan(reasoning: ReasoningConfig, *, disabled_value: str = "none") -> ReasoningPlan:
    if reasoning.mode == "disabled" or _is_disabled_reasoning_effort(reasoning.effort):
        return ReasoningPlan(sdk_params={"reasoning_effort": disabled_value})
    if reasoning.effort:
        return ReasoningPlan(sdk_params={"reasoning_effort": reasoning.effort})
    return ReasoningPlan()


def apply_reasoning_plan(params: dict[str, Any], plan: ReasoningPlan, *, override: bool = False) -> None:
    for warning in plan.warnings:
        logger.warning("Reasoning config resolution: %s", warning)

    for key, value in plan.sdk_params.items():
        if override or key not in params:
            params[key] = value

    if not plan.extra_body:
        return

    existing_extra_body = params.get("extra_body")
    if isinstance(existing_extra_body, Mapping):
        extra_body = dict(existing_extra_body)
        if override:
            extra_body.update(plan.extra_body)
        else:
            extra_body = {**plan.extra_body, **extra_body}
    elif existing_extra_body is not None:
        logger.warning(
            "Reasoning extra_body controls %s dropped: request extra_body is %s, not a mapping",
            sorted(plan.extra_body),
            type(existing_extra_body).__name__,
        )
        return
    else:
        extra_body = dict(plan.extra_body)
    params["extra_body"] = extra_body


def reasoning_request_controls(params: Mapping[str, Any]) -> dict[str, Any]:
    """Return only safe reasoning controls from a final provider request."""
    controls = {
        key: params[key]
        for key in _REASONING_WIRE_KEYS
        if key in params
    }
    extra_body = params.get("extra_body")
    if isinstance(extra_body, Mapping):
        extra_controls = {
            key: extra_body[key]
            for key in _REASONING_WIRE_KEYS
            if key in extra_body
        }
        if extra_controls:
            controls["extra_body"] = extra_controls
    return controls


def _is_disabled_reasoning_effort(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _DISABLED_REASONING_EFFORTS
