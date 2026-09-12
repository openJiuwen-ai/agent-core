# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility facade for OfficeQA document / search helpers."""

from __future__ import annotations

from openjiuwen.agent_evolving.skill_train.envs.officeqa import search_tools as _search
from openjiuwen.agent_evolving.skill_train.envs.officeqa.docs_paths import (
    resolve_candidate_files,
    resolve_docs_roots,
    run_tool,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.oracle_context import (
    build_oracle_parsed_pages_context,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.search_tools import custom_search

_SEARCH_DEFAULT_EXPORTS = frozenset(
    {
        "DEFAULT_CUSTOM_SEARCH_AUTH_ENV",
        "DEFAULT_CUSTOM_SEARCH_INITIAL_BACKOFF_SECONDS",
        "DEFAULT_CUSTOM_SEARCH_MAX_RESULTS",
        "DEFAULT_CUSTOM_SEARCH_MAX_RETRIES",
        "DEFAULT_CUSTOM_SEARCH_PROVIDER",
        "DEFAULT_CUSTOM_SEARCH_TIMEOUT",
        "DEFAULT_CUSTOM_SEARCH_URL",
        "DEFAULT_USER_AGENT",
    }
)

__all__ = [
    "DEFAULT_CUSTOM_SEARCH_AUTH_ENV",
    "DEFAULT_CUSTOM_SEARCH_INITIAL_BACKOFF_SECONDS",
    "DEFAULT_CUSTOM_SEARCH_MAX_RESULTS",
    "DEFAULT_CUSTOM_SEARCH_MAX_RETRIES",
    "DEFAULT_CUSTOM_SEARCH_PROVIDER",
    "DEFAULT_CUSTOM_SEARCH_TIMEOUT",
    "DEFAULT_CUSTOM_SEARCH_URL",
    "DEFAULT_USER_AGENT",
    "build_oracle_parsed_pages_context",
    "custom_search",
    "resolve_candidate_files",
    "resolve_docs_roots",
    "run_tool",
]


def __getattr__(name: str) -> object:
    if name in _SEARCH_DEFAULT_EXPORTS:
        return getattr(_search, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
