# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Known JEV System One providers: default model, API base, key variable and endpoint path."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class JevProvider:
    """Defaults for one JEV provider. ``api_key_env`` is the variable name, never the key."""

    model: str
    api_base: str
    api_key_env: str
    endpoint_path: str


JEV_PROVIDERS: dict[str, JevProvider] = {
    "typesafe": JevProvider("jev-1.13.0", "https://api.typesafe.ai/v1", "TYPESAFE_API_KEY", "/systemone"),
    "openrouter": JevProvider(
        "typesafe/jev-1.13", "https://openrouter.ai/api/alpha", "OPENROUTER_API_KEY", "/decisions"
    ),
}


def jev_defaults_for(api_base: str) -> tuple[str, str, str]:
    """Derive ``(endpoint_path, model, api_key_env)`` for a JEV-format service from its base URL.

    Only TypeSafe serves ``/systemone``; every other JEV-format service (OpenRouter, MCL, Yale, ...)
    uses the generic ``/decisions``. The model and key-variable defaults follow the host: a TypeSafe
    host gets TypeSafe's, an OpenRouter host gets OpenRouter's, any other host has no model default.
    """
    base = api_base.strip().rstrip("/")
    host = (urlsplit(base).hostname or "").lower()
    if host == "typesafe.ai" or host.endswith(".typesafe.ai"):
        typesafe = JEV_PROVIDERS["typesafe"]
        return "/systemone", typesafe.model, typesafe.api_key_env
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        openrouter = JEV_PROVIDERS["openrouter"]
        return "/decisions", openrouter.model, openrouter.api_key_env
    return "/decisions", "", "DUPLEX_ROUTER_API_KEY"
