# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OfficeQA DatasetEnvAdapter — split loading + batch rollout wiring."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.agent_evolving.skill_train.envs.dataset_adapter import DatasetEnvAdapter
from openjiuwen.agent_evolving.skill_train.envs.officeqa.dataloader import OfficeQADataLoader
from openjiuwen.agent_evolving.skill_train.envs.officeqa.rollout import OfficeQABatchKnobs, run_batch

_ENV_LOOKUP_ENDPOINT = os.environ.get(
    "OFFICEQA_SEARCH_API_URL",
    "http://localhost:8080/search_tool/search",
)
_ENV_AUTH_VAR = "OFFICEQA_CUSTOM_SEARCH_AUTH"


@dataclass(frozen=True)
class _SearchTransport:
    endpoint: str = _ENV_LOOKUP_ENDPOINT
    auth_var: str = _ENV_AUTH_VAR
    vendor: str = "duckduckgo"
    hit_limit: int = 4
    timeout_s: int = 20


@dataclass(frozen=True)
class OfficeQARolloutSettings:
    """Public rollout settings (search / tools / parallelism)."""

    workers: int = 8
    max_tool_turns: int = 12
    max_completion_tokens: int = 16384
    search_mode: str = "offline"
    max_queries_per_turn: int = 4
    transport: _SearchTransport = field(default_factory=_SearchTransport)
    use_local_tools: bool = True
    data_dirs: list[str] | str | None = None

    @property
    def search_api_url(self) -> str:
        return self.transport.endpoint

    @property
    def search_auth_env(self) -> str:
        return self.transport.auth_var

    @property
    def search_provider(self) -> str:
        return self.transport.vendor

    @property
    def search_max_num_results(self) -> int:
        return self.transport.hit_limit

    @property
    def search_timeout_seconds(self) -> int:
        return self.transport.timeout_s

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> OfficeQARolloutSettings:
        corpus = raw.get("data_dirs")
        if corpus is None:
            corpus = raw.get("docs_dirs")
        endpoint = str(raw.get("search_api_url") or "").strip() or _ENV_LOOKUP_ENDPOINT
        transport = _SearchTransport(
            endpoint=endpoint,
            auth_var=str(raw.get("search_auth_env") or _ENV_AUTH_VAR).strip(),
            vendor=str(raw.get("search_provider") or "duckduckgo").strip(),
            hit_limit=int(raw.get("search_max_num_results", 4) or 4),
            timeout_s=int(raw.get("search_timeout_seconds", 20) or 20),
        )
        return cls(
            workers=int(raw.get("workers", 8) or 8),
            max_tool_turns=int(raw.get("max_tool_turns", 12) or 12),
            max_completion_tokens=int(raw.get("max_completion_tokens", 16384) or 16384),
            search_mode=str(raw.get("search_mode") or "offline"),
            max_queries_per_turn=int(raw.get("max_queries_per_turn", 4) or 4),
            transport=transport,
            use_local_tools=bool(raw.get("use_local_tools", True)),
            data_dirs=corpus,
        )


class OfficeQAAdapter(DatasetEnvAdapter):
    """Connect OfficeQA splits to skill_train rollouts."""

    def __init__(self, **kwargs: Any) -> None:
        # Reflect / analyst knobs (EnvAdapter.reflect consumers).
        self.analyst_workers = int(kwargs.get("analyst_workers", 8) or 8)
        self.failure_only = bool(kwargs.get("failure_only", False))
        self.minibatch_size = int(kwargs.get("minibatch_size", 8) or 8)
        self.edit_budget = int(kwargs.get("edit_budget", 4) or 4)

        self.apply_rollout_settings(OfficeQARolloutSettings.from_mapping(kwargs))

        self.dataloader = OfficeQADataLoader(
            split_dir=str(kwargs.get("split_dir", "") or ""),
            data_path=str(kwargs.get("data_path", "") or ""),
            split_mode=str(kwargs.get("split_mode", "split_dir") or "split_dir"),
            split_ratio=str(kwargs.get("split_ratio", "2:1:7") or "2:1:7"),
            split_seed=int(kwargs.get("split_seed", 42) or 42),
            split_output_dir=str(kwargs.get("split_output_dir", "") or ""),
            seed=int(kwargs.get("seed", 42) or 42),
            limit=int(kwargs.get("limit", 0) or 0),
        )

    def apply_rollout_settings(self, settings: OfficeQARolloutSettings) -> None:
        """Mirror rollout settings onto public adapter attributes."""
        self.settings = settings
        self.workers = settings.workers
        self.max_tool_turns = settings.max_tool_turns
        self.max_completion_tokens = settings.max_completion_tokens
        self.search_mode = settings.search_mode
        self.max_queries_per_turn = settings.max_queries_per_turn
        self.search_api_url = settings.search_api_url
        self.search_auth_env = settings.search_auth_env
        self.search_provider = settings.search_provider
        self.search_max_num_results = settings.search_max_num_results
        self.search_timeout_seconds = settings.search_timeout_seconds
        self.use_local_tools = settings.use_local_tools
        self.data_dirs = settings.data_dirs

    def _to_batch_knobs(
        self,
        out_dir: str,
        skill_content: str,
        extras: dict,
    ) -> OfficeQABatchKnobs:
        cfg = self.settings
        return OfficeQABatchKnobs.from_flat(
            out_dir,
            skill_content,
            workers=cfg.workers,
            max_tool_turns=cfg.max_tool_turns,
            max_completion_tokens=cfg.max_completion_tokens,
            search_mode=cfg.search_mode,
            max_queries_per_turn=cfg.max_queries_per_turn,
            search_api_url=cfg.search_api_url,
            search_auth_env=cfg.search_auth_env,
            search_provider=cfg.search_provider,
            search_max_num_results=cfg.search_max_num_results,
            search_timeout_seconds=cfg.search_timeout_seconds,
            use_local_tools=cfg.use_local_tools,
            data_dirs=cfg.data_dirs,
            diagnostic_mode=bool(extras.get("diagnostic_mode", False)),
            diagnostic_instruction=str(extras.get("diagnostic_instruction", "")),
        )

    def rollout(self, env_manager, skill_content: str, out_dir: str, **kwargs) -> list[dict]:
        items = list(env_manager)
        return run_batch(items, self._to_batch_knobs(out_dir, skill_content, kwargs))

    def get_task_types(self) -> list[str]:
        return self.collect_task_types("officeqa")
