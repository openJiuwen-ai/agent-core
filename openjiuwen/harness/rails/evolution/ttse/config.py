# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TTSE configuration.

A single dataclass that mirrors the constants of the original TTSE
``config.py`` (paths, bank caps, retrieval/dedup knobs) so a DeepAgent can opt
into Two-Track Self-Evolution via ``DeepAgentConfig.ttse_config``.

Unlike the file-global constants in the reference, every knob lives here and is
passed explicitly to :class:`TTSERail`, keeping the feature off by default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    GENERATE_RECORDS_LLM_POLICY,
)
from openjiuwen.core.memory.lite.embeddings import EmbeddingProvider


@dataclass
class TTSEConfig:
    """Configuration for the TTSE rail (FACT + TIP dual-track self-evolution).

    Attributes:
        store_path: JSON path for the shared FACT/TIP bank (created on first write).
        embedding: Optional embedding provider. When set, dedup uses cosine
            similarity (semantic) and retrieval injects the top-K most relevant
            rules. When ``None``, dedup falls back to substring matching and the
            whole bank is injected (the reference's legacy mode).
            Callers typically construct
            ``OpenAICompatibleEmbeddingProvider(api_key=..., base_url=..., model=...)``
            (e.g. Huawei MaaS ``bge-m3`` at ``https://api.modelarts-maas.com/v1``)
            and assign it here; do not put raw url/key strings on TTSEConfig.
        dedup_threshold: Cosine threshold above which two rules are treated as
            duplicates during induction. Ignored when ``embedding`` is None.
        max_facts / max_tips: Hard caps on bank size (highest-count kept).
        top_k_facts / top_k_tips: How many rules to inject per task when a
            retrieval embedding provider is configured.
        traj_char_budget: Max chars of trajectory text fed to the induce prompt.
            Overflow keeps the tail (actions/observations), not the USER head.
        inject_enabled: Inject the bank (or top-K) into the system prompt.
        inject_mode: Where FACT/TIP land. Default ``disk_catalog`` leaves a
            fixed guidance section, trails the category listing as a prompt
            attachment, and exposes ``ttse_consult(category=)`` for FACT/TIP.
            ``legacy_system`` keeps the P:45 body (FACT/TIP dumped into the
            system prompt). ``trailing_attach`` is accepted but currently
            falls back to ``legacy_system``.
        evolve_enabled: Run induction after each task to grow the bank.
        success_threshold: Score >= this counts as success (Slice 3 gating).
        induce_llm_policy: LLM invocation policy for induce/blame/synthesize.
        batch_size: Cost-amortization knob. When > 1, per-task observations are
            buffered and induced together via ONE ``induce_batch`` LLM call every
            ``batch_size`` tasks (blame/retire still run per failed task). 1 =
            induce on every task (default, the reference's per-task mode).
        batch_traj_budget: Per-task trajectory chars kept in the batch buffer
            (each task's excerpt is capped before the single batch induce call).
        consult_max_chars / consult_max_rules: Truncation for ``ttse_consult``.
        detect_min_tool_calls: Min tool calls before reply-delivery detect runs.
        detect_max_output_paths: Cap on extracted write paths (artifact gate).
        detect_final_reply_chars: Max chars of final assistant reply fed to Judge.
        detect_llm_policy: Short policy for the one-shot reply-delivery Judge.
        dream_enabled: Run periodic Auto-dream bank hygiene.
        dream_interval: Non-follow-up task iterations between dream attempts.
        dream_min_hours: Min hours since last successful dream.
        dream_min_rules: Skip LLM merge when facts+tips below this (prune/purge still run).
        dream_soft_lo: Cosine edge threshold for soft clustering near-duplicates.
        dream_cluster_min_size: Min cluster size to consider for merge.
        dream_max_llm_merges: Cap LLM merge calls per dream run.
        dream_ttl_days: Retire rules not injected for this many days.
        dream_prune_enabled: Enable TTL prune pass.
        dream_prune_mode: ``retire`` (default) or ``delete``.
        dream_purge_tips_enabled: Enable deterministic low-quality TIP purge.
        dream_state_path: Optional path for dream-state.json; derived from store_path when empty.
    """

    store_path: str = ".ttse/bank.json"
    embedding: Optional[EmbeddingProvider] = None
    dedup_threshold: float = 0.88
    max_facts: int = 400
    max_tips: int = 400
    top_k_facts: int = 10
    top_k_tips: int = 10
    traj_char_budget: int = 9000
    inject_enabled: bool = True
    inject_mode: str = "disk_catalog"
    evolve_enabled: bool = True
    success_threshold: float = 0.999
    induce_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY
    batch_size: int = 1
    batch_traj_budget: int = 1100
    consult_max_chars: int = 8000
    consult_max_rules: int = 40
    detect_min_tool_calls: int = 5
    detect_max_output_paths: int = 20
    detect_final_reply_chars: int = 1500
    detect_llm_policy: LLMInvokePolicy = field(
        default_factory=lambda: LLMInvokePolicy(
            attempt_timeout_secs=30.0,
            total_budget_secs=35.0,
            max_attempts=1,
        )
    )
    # Auto-dream (bank hygiene)
    dream_enabled: bool = True
    dream_interval: int = 20
    dream_min_hours: float = 24.0
    dream_min_rules: int = 8
    dream_soft_lo: float = 0.72
    dream_cluster_min_size: int = 2
    dream_max_llm_merges: int = 10
    dream_ttl_days: int = 90
    dream_prune_enabled: bool = True
    dream_prune_mode: str = "retire"  # or "delete"
    dream_purge_tips_enabled: bool = True
    dream_state_path: str = ""

    def resolved_dream_state_path(self) -> str:
        """Path for dream-state.json (same directory as the bank by default)."""
        if self.dream_state_path:
            return self.dream_state_path
        directory = os.path.dirname(self.store_path) or ".ttse"
        return os.path.join(directory, "dream-state.json")

    def is_disk_catalog(self) -> bool:
        """True when FACT/TIP are disclosed via ``ttse_consult``, not P:45.

        The category listing is trailed as a prompt attachment; P:45 is guidance only.
        """
        return str(self.inject_mode or "").strip() == "disk_catalog"


__all__ = ["TTSEConfig"]
