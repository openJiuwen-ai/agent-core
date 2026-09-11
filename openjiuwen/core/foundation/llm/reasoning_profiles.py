# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Declarative reasoning capabilities for built-in provider/model exceptions.

Models not listed here inherit the standard behavior of their selected
protocol. Keep this table provider-scoped: the same model name can expose
different controls through a different gateway or subscription endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any


@dataclass(frozen=True)
class ReasoningCapability:
    """User-selectable reasoning values and an optional recommendation."""

    options: tuple[str, ...] = ()
    recommended: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"options": list(self.options), "recommended": self.recommended}


@dataclass(frozen=True)
class ReasoningProfile:
    """One provider-scoped model family and its protocol wire encoders."""

    provider: str
    model_patterns: tuple[str, ...]
    capability: ReasoningCapability
    openai_wire: str
    anthropic_wire: str
    anthropic_capability: ReasoningCapability | None = None


TOGGLE = ReasoningCapability(options=("off", "on"), recommended="on")
TOGGLE_WITH_BUDGET = TOGGLE
ALWAYS_ON = ReasoningCapability()
ALWAYS_ON_WITH_BUDGET = ALWAYS_ON
UNSUPPORTED = ReasoningCapability()

# Budget-only Anthropic thinking (Claude Sonnet 4.5 and earlier): the API has
# no effort field, so labeled levels are encoded as budget_tokens by the
# "anthropic_manual" wire (low=2048, medium=8192, high=16384; API minimum is
# 1024, official guidance is ~1-2k for simple tasks and 16k+ for complex ones).
ANTHROPIC_MANUAL_BUDGET = ReasoningCapability(
    options=("off", "low", "medium", "high"),
    recommended="medium",
)

DEEPSEEK_V4 = ReasoningCapability(
    options=("off", "low", "high", "max"),
    recommended="high",
)
GLM_52 = ReasoningCapability(
    options=("off", "high", "max"),
    recommended="max",
)
QWEN_38 = ReasoningCapability(
    options=("off", "low", "medium", "xhigh"),
    recommended="xhigh",
)
KIMI_K3 = ReasoningCapability(
    options=("low", "high", "max"),
    recommended="high",
)
VOLCENGINE_SEED_20 = ReasoningCapability(
    options=("off", "low", "medium", "high"),
    recommended="medium",
)
OPENAI_GPT_56 = ReasoningCapability(
    options=("off", "low", "medium", "high", "xhigh", "max"),
    recommended="medium",
)
OPENAI_GPT_55 = ReasoningCapability(
    options=("off", "low", "medium", "high", "xhigh"),
    recommended="medium",
)
OPENAI_GPT_51_TO_54 = ReasoningCapability(
    options=("off", "low", "medium", "high", "xhigh"),
    recommended="off",
)
OPENAI_GPT_5 = ReasoningCapability(
    options=("minimal", "low", "medium", "high"),
    recommended="medium",
)


# Ordered from exact/specific families to broad families. This is intentionally
# data, rather than model-name conditionals in the request resolver, so it can
# migrate directly into the future LLM Registry ModelProfile catalog.
REASONING_PROFILES: tuple[ReasoningProfile, ...] = (
    # First-party protocol models with narrower behavior than the generic
    # protocol defaults.
    ReasoningProfile("openai", ("gpt-5.6*",), OPENAI_GPT_56, "standard_openai", "standard_anthropic"),
    ReasoningProfile("openai", ("gpt-5.5*",), OPENAI_GPT_55, "standard_openai", "standard_anthropic"),
    ReasoningProfile(
        "openai",
        ("gpt-5.4*", "gpt-5.3*", "gpt-5.2*", "gpt-5.1*"),
        OPENAI_GPT_51_TO_54,
        "standard_openai",
        "standard_anthropic",
    ),
    ReasoningProfile("openai", ("gpt-5*",), OPENAI_GPT_5, "standard_openai", "standard_anthropic"),
    ReasoningProfile(
        "anthropic",
        ("claude-sonnet-4-5*", "claude-sonnet-4.5*"),
        ANTHROPIC_MANUAL_BUDGET,
        "standard_openai",
        "anthropic_manual",
    ),

    # OpenRouter exposes one normalized reasoning object for both API shapes.
    ReasoningProfile("openrouter", ("openai/gpt-5.6-*",), OPENAI_GPT_56, "openrouter", "openrouter"),
    ReasoningProfile(
        "openrouter",
        ("qwen/qwen3.8-max*",),
        QWEN_38,
        "openrouter",
        "openrouter",
    ),
    ReasoningProfile(
        "openrouter",
        ("z-ai/glm-5.2*",),
        ReasoningCapability(options=("off", "high", "xhigh"), recommended="high"),
        "openrouter",
        "openrouter",
    ),
    ReasoningProfile(
        "openrouter",
        ("deepseek/deepseek-v4-pro*",),
        ReasoningCapability(options=("off", "high", "xhigh"), recommended="high"),
        "openrouter",
        "openrouter",
    ),
    ReasoningProfile("openrouter", ("moonshotai/kimi-k3*",), KIMI_K3, "openrouter", "openrouter"),
    ReasoningProfile("openrouter", ("minimax/minimax-m3*",), TOGGLE, "openrouter", "openrouter"),
    ReasoningProfile(
        "openrouter",
        ("anthropic/claude-sonnet-4.5*",),
        TOGGLE,
        "openrouter",
        "openrouter",
    ),
    ReasoningProfile(
        "openrouter",
        ("google/gemini-2.5-flash*", "poolside/laguna-s-2.1*"),
        TOGGLE_WITH_BUDGET,
        "openrouter",
        "openrouter",
    ),

    # Alibaba Model Studio / DashScope.
    # The Model Studio Anthropic endpoint supports output_config.effort for
    # qwen3.8-max (low/medium/xhigh, default xhigh) and marks budget_tokens as
    # deprecated, so both protocols expose the same labeled levels.
    ReasoningProfile(
        "dashscope",
        ("qwen3.8-max*",),
        QWEN_38,
        "qwen38",
        "qwen38_anthropic",
    ),
    ReasoningProfile(
        "dashscope",
        ("qwen3-vl-*-thinking*",),
        ALWAYS_ON_WITH_BUDGET,
        "always_on",
        "always_on",
    ),
    ReasoningProfile(
        "dashscope",
        ("qwen3*", "qwen-plus*", "qwen-max*", "qwen-flash*"),
        TOGGLE_WITH_BUDGET,
        "enable_thinking",
        "dashscope_budget",
    ),
    ReasoningProfile("dashscope", ("deepseek-v4-*",), DEEPSEEK_V4, "deepseek", "deepseek_anthropic"),
    # Same Model Studio endpoint: output_config.effort supports high/max for
    # glm-5.2 (default max), so the Anthropic protocol keeps the GLM levels.
    ReasoningProfile(
        "dashscope",
        ("glm-5.2*",),
        GLM_52,
        "glm",
        "dashscope_glm_anthropic",
    ),
    ReasoningProfile(
        "dashscope",
        ("glm-5*", "glm-4.7*", "kimi-k2.6*", "kimi-k2.5*"),
        TOGGLE_WITH_BUDGET,
        "thinking_toggle",
        "anthropic_toggle",
    ),
    ReasoningProfile("dashscope", ("kimi-k3*",), KIMI_K3, "kimi_k3", "kimi_k3"),

    # DeepSeek official.
    ReasoningProfile("deepseek", ("deepseek-v4-*",), DEEPSEEK_V4, "deepseek", "deepseek_anthropic"),

    # Moonshot/Kimi official and Coding Plan.
    ReasoningProfile("moonshot", ("kimi-k3*",), KIMI_K3, "kimi_k3", "kimi_k3"),
    ReasoningProfile("moonshot", ("kimi-k2.7-code*", "kimi-k2-thinking*"), ALWAYS_ON, "always_on", "always_on"),
    ReasoningProfile("moonshot", ("kimi-k2.6*", "kimi-k2.5*"), TOGGLE, "thinking_toggle", "anthropic_toggle"),

    # MiniMax M3 is hybrid; M2/M1 families are native reasoning models without
    # a documented disable or effort control.
    ReasoningProfile("minimax", ("minimax-m3*",), TOGGLE, "minimax_m3", "minimax_m3_anthropic"),
    ReasoningProfile(
        "minimax",
        ("minimax-m2*", "minimax-m1*"),
        ALWAYS_ON,
        "minimax_m2",
        "always_on",
    ),

    # Zhipu official and Coding Plan.
    ReasoningProfile("zhipu", ("glm-5.2*",), GLM_52, "glm", "glm_anthropic"),
    ReasoningProfile(
        "zhipu",
        ("glm-5*", "glm-4.7*"),
        TOGGLE,
        "thinking_toggle",
        "anthropic_toggle",
    ),
    ReasoningProfile("zhipu", ("codegeex-4*",), UNSUPPORTED, "unsupported", "unsupported"),

    # Huawei Cloud ModelArts MaaS. The former pangu-ultra/large/small aliases
    # are not MaaS V2 model IDs and are deliberately not profiled.
    ReasoningProfile(
        "modelarts",
        ("openpangu-2.0-*", "glm-5.1*", "kimi-k2.6*", "qwen3-32b*", "qwen3-30b-a3b*"),
        TOGGLE,
        "thinking_toggle",
        "anthropic_toggle",
    ),
    ReasoningProfile("modelarts", ("glm-5.2*",), GLM_52, "glm", "glm_anthropic"),
    ReasoningProfile(
        "modelarts",
        ("deepseek-v4-*",),
        TOGGLE,
        "modelarts_deepseek",
        "modelarts_deepseek_anthropic",
    ),

    # Volcano Engine Ark.
    ReasoningProfile(
        "volcengine",
        ("seed-2.0-*", "seed-2-0-*", "doubao-seed-2-0-*", "doubao-seed-2.0-*"),
        VOLCENGINE_SEED_20,
        "volcengine_seed",
        "standard_anthropic",
    ),
    ReasoningProfile(
        "volcengine",
        ("seed-1.6*", "doubao-seed-1-6*"),
        TOGGLE,
        "thinking_toggle",
        "anthropic_toggle",
    ),
    ReasoningProfile("volcengine", ("deepseek-v4-*",), DEEPSEEK_V4, "deepseek", "deepseek_anthropic"),
    ReasoningProfile("volcengine", ("doubao-1.5-pro*",), UNSUPPORTED, "unsupported", "unsupported"),

    # Baidu Qianfan, including models proxied from other vendors.
    ReasoningProfile("qianfan", ("deepseek-v4-*",), DEEPSEEK_V4, "deepseek", "deepseek_anthropic"),
    ReasoningProfile("qianfan", ("glm-5.2*",), TOGGLE, "thinking_toggle", "anthropic_toggle"),
    ReasoningProfile(
        "qianfan",
        ("glm-5*", "kimi-k2.6*", "kimi-k2.5*", "mimo-v2.5*"),
        TOGGLE,
        "thinking_toggle",
        "anthropic_toggle",
    ),
    # "emie-5.*" is intentional typo tolerance: some Qianfan catalog listings
    # expose the misspelled ID alongside the canonical "ernie" name.
    ReasoningProfile(
        "qianfan",
        ("ernie-5.*", "emie-5.*"),
        TOGGLE,
        "qianfan_toggle",
        "anthropic_toggle",
    ),
    ReasoningProfile("qianfan", ("qianfan-ocr-*",), UNSUPPORTED, "unsupported", "unsupported"),

    # Xiaomi MiMo official and Token Plan use the same request format.
    # "mino-v2.5*" is intentional typo tolerance for misspelled catalog IDs.
    ReasoningProfile("mimo", ("mimo-v2.5*", "mino-v2.5*"), TOGGLE, "thinking_toggle", "anthropic_toggle"),

    # endpoint_profile="vllm" is a wire override, not a capability table.
    # Capability still comes from MODEL_REASONING_FALLBACKS (GLM-5.2 keeps
    # off/high/max, DeepSeek-V4 keeps off/low/high/max). The resolver forces
    # DashScope-style enable_thinking + thinking_budget regardless of the
    # fallback's official thinking.type encoder.
)


# Provider-specific profiles above always win. These model-name fallbacks cover
# custom OpenAI/Anthropic-compatible endpoints whose host does not reveal the
# upstream vendor. They describe the model family's usual wire format; unknown
# models still fall through to the conservative protocol defaults below.
MODEL_REASONING_FALLBACKS: tuple[ReasoningProfile, ...] = (
    ReasoningProfile("*", ("gpt-5.6*",), OPENAI_GPT_56, "standard_openai", "standard_anthropic"),
    ReasoningProfile("*", ("gpt-5.5*",), OPENAI_GPT_55, "standard_openai", "standard_anthropic"),
    ReasoningProfile(
        "*",
        ("gpt-5.4*", "gpt-5.3*", "gpt-5.2*", "gpt-5.1*"),
        OPENAI_GPT_51_TO_54,
        "standard_openai",
        "standard_anthropic",
    ),
    ReasoningProfile("*", ("gpt-5*",), OPENAI_GPT_5, "standard_openai", "standard_anthropic"),
    # Custom Anthropic-compatible endpoints must not fall through to the
    # protocol default (which includes max for adaptive Claude). Sonnet 4.5
    # is budget-only: off/low/medium/high, no max.
    ReasoningProfile(
        "*",
        ("claude-sonnet-4-5*", "claude-sonnet-4.5*"),
        ANTHROPIC_MANUAL_BUDGET,
        "standard_openai",
        "anthropic_manual",
    ),
    ReasoningProfile("*", ("qwen3.8-max*",), QWEN_38, "qwen38", "qwen38_anthropic"),
    ReasoningProfile("*", ("qwen3-vl-*-thinking*",), ALWAYS_ON_WITH_BUDGET, "always_on", "always_on"),
    ReasoningProfile(
        "*",
        ("qwen3*", "qwen-plus*", "qwen-max*", "qwen-flash*"),
        TOGGLE_WITH_BUDGET,
        "enable_thinking",
        "dashscope_budget",
    ),
    ReasoningProfile("*", ("deepseek-v4-*",), DEEPSEEK_V4, "deepseek", "deepseek_anthropic"),
    ReasoningProfile("*", ("kimi-k3*",), KIMI_K3, "kimi_k3", "kimi_k3"),
    ReasoningProfile("*", ("kimi-k2.7-code*", "kimi-k2-thinking*"), ALWAYS_ON, "always_on", "always_on"),
    ReasoningProfile("*", ("kimi-k2.6*", "kimi-k2.5*"), TOGGLE, "thinking_toggle", "anthropic_toggle"),
    ReasoningProfile("*", ("minimax-m3*",), TOGGLE, "minimax_m3", "minimax_m3_anthropic"),
    ReasoningProfile("*", ("minimax-m2*", "minimax-m1*"), ALWAYS_ON, "minimax_m2", "always_on"),
    ReasoningProfile("*", ("glm-5.2*",), GLM_52, "glm", "glm_anthropic"),
    ReasoningProfile("*", ("glm-5*", "glm-4.7*"), TOGGLE, "thinking_toggle", "anthropic_toggle"),
    ReasoningProfile("*", ("codegeex-4*",), UNSUPPORTED, "unsupported", "unsupported"),
    ReasoningProfile("*", ("openpangu-2.0-*",), TOGGLE, "thinking_toggle", "anthropic_toggle"),
    ReasoningProfile(
        "*",
        ("seed-2.0-*", "seed-2-0-*", "doubao-seed-2-0-*", "doubao-seed-2.0-*"),
        VOLCENGINE_SEED_20,
        "volcengine_seed",
        "standard_anthropic",
    ),
    ReasoningProfile("*", ("seed-1.6*", "doubao-seed-1-6*"), TOGGLE, "thinking_toggle", "anthropic_toggle"),
    ReasoningProfile("*", ("doubao-1.5-pro*", "qianfan-ocr-*"), UNSUPPORTED, "unsupported", "unsupported"),
    ReasoningProfile("*", ("ernie-5.*", "emie-5.*"), TOGGLE, "qianfan_toggle", "anthropic_toggle"),
    ReasoningProfile("*", ("mimo-v2.5*", "mino-v2.5*"), TOGGLE, "thinking_toggle", "anthropic_toggle"),
)


STANDARD_OPENAI_CAPABILITY = ReasoningCapability(
    options=("off", "low", "medium", "high"),
)
STANDARD_ANTHROPIC_CAPABILITY = ReasoningCapability(
    options=("off", "low", "medium", "high", "max"),
)


def find_reasoning_profile(provider: str, model: str) -> ReasoningProfile | None:
    provider_key = str(provider or "").strip().lower().replace("_", "-")
    model_key = str(model or "").strip().lower()
    for profile in REASONING_PROFILES:
        if profile.provider != provider_key:
            continue
        if any(fnmatchcase(model_key, pattern) for pattern in profile.model_patterns):
            return profile
    fallback_model_keys = {model_key, model_key.rsplit("/", 1)[-1]}
    for profile in MODEL_REASONING_FALLBACKS:
        if any(
            fnmatchcase(candidate, pattern)
            for candidate in fallback_model_keys
            for pattern in profile.model_patterns
        ):
            return profile
    return None


def standard_reasoning_capability(protocol: str) -> ReasoningCapability:
    if str(protocol).strip().lower() == "anthropic_messages":
        return STANDARD_ANTHROPIC_CAPABILITY
    return STANDARD_OPENAI_CAPABILITY


def profile_reasoning_capability(profile: ReasoningProfile, protocol: str) -> ReasoningCapability:
    if str(protocol).strip().lower() == "anthropic_messages" and profile.anthropic_capability is not None:
        return profile.anthropic_capability
    return profile.capability


__all__ = [
    "REASONING_PROFILES",
    "MODEL_REASONING_FALLBACKS",
    "ReasoningCapability",
    "ReasoningProfile",
    "find_reasoning_profile",
    "profile_reasoning_capability",
    "standard_reasoning_capability",
]
