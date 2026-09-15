# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility facade for OfficeQA document / search helpers."""

from __future__ import annotations

from openjiuwen.agent_evolving.skill_train.envs.officeqa.docs_paths import (
    resolve_candidate_files,
    resolve_docs_roots,
    run_tool,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.oracle_context import (
    build_oracle_parsed_pages_context,
)
from openjiuwen.agent_evolving.skill_train.envs.officeqa.search_tools import (
    DEFAULT_CUSTOM_SEARCH_AUTH_ENV,
    DEFAULT_CUSTOM_SEARCH_INITIAL_BACKOFF_SECONDS,
    DEFAULT_CUSTOM_SEARCH_MAX_RESULTS,
    DEFAULT_CUSTOM_SEARCH_MAX_RETRIES,
    DEFAULT_CUSTOM_SEARCH_PROVIDER,
    DEFAULT_CUSTOM_SEARCH_TIMEOUT,
    DEFAULT_CUSTOM_SEARCH_URL,
    DEFAULT_USER_AGENT,
    custom_search,
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
