# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared env parsing and auxiliary LLM builders for trajectory recording."""

from __future__ import annotations

import os
import sys
from typing import Any, Sequence

from openjiuwen.core.foundation.llm import init_model

_DEFAULT_API_BASE = "https://api.openai.com/v1"
_DEFAULT_PROVIDER = "OpenAI"

_SHARED_API_KEY_KEYS = ("API_KEY", "LLM_API_KEY")
_SHARED_API_BASE_KEYS = ("API_BASE", "LLM_API_BASE")
_SHARED_PROVIDER_KEYS = ("MODEL_PROVIDER", "LLM_PROVIDER")


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes")


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def first_env(*keys: str, default: str = "") -> str:
    for key in keys:
        raw = os.getenv(key)
        if raw is not None and raw.strip():
            return raw.strip()
    return default


def resolve_ssl_verify(*keys: str) -> bool:
    for key in keys:
        if os.getenv(key) is not None and os.getenv(key, "").strip() != "":
            return env_bool(key, False)
    return env_bool("LLM_SSL_VERIFY", False)


def build_auxiliary_model(
    *,
    default_model: Any,
    model_name_keys: Sequence[str],
    api_key_keys: Sequence[str],
    api_base_keys: Sequence[str],
    provider_keys: Sequence[str],
    ssl_verify_keys: Sequence[str],
    max_tokens: int,
    require_api_key: bool = False,
    log_label: str | None = None,
) -> Any:
    """Build an optional auxiliary model from prefixed env vars with shared fallbacks."""
    model_name = first_env(*model_name_keys)
    if not model_name:
        if log_label:
            print(f"{log_label}=main_model")
        return default_model

    api_key = first_env(*api_key_keys)
    if not api_key:
        if require_api_key:
            print(
                "Missing API key. Set one of: "
                + ", ".join(api_key_keys),
                file=sys.stderr,
            )
            sys.exit(1)
        return default_model

    if log_label:
        print(f"{log_label}={model_name!r}")

    return init_model(
        provider=first_env(*provider_keys, default=_DEFAULT_PROVIDER),
        model_name=model_name,
        api_key=api_key,
        api_base=first_env(*api_base_keys, default=_DEFAULT_API_BASE),
        temperature=0,
        max_tokens=max_tokens,
        verify_ssl=resolve_ssl_verify(*ssl_verify_keys),
    )


def build_classifier_model(default_model: Any) -> Any:
    """Build an optional cheaper VLM for trajectory screenshot classification."""
    return build_auxiliary_model(
        default_model=default_model,
        model_name_keys=("CLASSIFIER_MODEL_NAME", "CLASSIFIER_LLM_MODEL_NAME"),
        api_key_keys=(
            "CLASSIFIER_API_KEY",
            "CLASSIFIER_LLM_API_KEY",
            *_SHARED_API_KEY_KEYS,
        ),
        api_base_keys=(
            "CLASSIFIER_API_BASE",
            "CLASSIFIER_LLM_API_BASE",
            *_SHARED_API_BASE_KEYS,
        ),
        provider_keys=(
            "CLASSIFIER_MODEL_PROVIDER",
            "CLASSIFIER_LLM_PROVIDER",
            *_SHARED_PROVIDER_KEYS,
        ),
        ssl_verify_keys=("CLASSIFIER_LLM_SSL_VERIFY", "LLM_SSL_VERIFY"),
        max_tokens=120,
        require_api_key=True,
        log_label="classifier_model",
    )


def build_action_summary_model(default_model: Any) -> Any:
    """Build text model for per-turn ``agent_action`` summarization."""
    return build_auxiliary_model(
        default_model=default_model,
        model_name_keys=(
            "ACTION_SUMMARY_MODEL_NAME",
            "CLASSIFIER_MODEL_NAME",
            "CLASSIFIER_LLM_MODEL_NAME",
        ),
        api_key_keys=(
            "ACTION_SUMMARY_API_KEY",
            "CLASSIFIER_API_KEY",
            "CLASSIFIER_LLM_API_KEY",
            *_SHARED_API_KEY_KEYS,
        ),
        api_base_keys=(
            "ACTION_SUMMARY_API_BASE",
            "CLASSIFIER_API_BASE",
            "CLASSIFIER_LLM_API_BASE",
            *_SHARED_API_BASE_KEYS,
        ),
        provider_keys=(
            "ACTION_SUMMARY_MODEL_PROVIDER",
            "CLASSIFIER_MODEL_PROVIDER",
            "CLASSIFIER_LLM_PROVIDER",
            *_SHARED_PROVIDER_KEYS,
        ),
        ssl_verify_keys=(
            "ACTION_SUMMARY_LLM_SSL_VERIFY",
            "CLASSIFIER_LLM_SSL_VERIFY",
            "LLM_SSL_VERIFY",
        ),
        max_tokens=40,
        require_api_key=False,
    )
