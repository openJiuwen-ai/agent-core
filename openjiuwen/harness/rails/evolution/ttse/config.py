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

from dataclasses import dataclass
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
        dedup_threshold: Cosine threshold above which two rules are treated as
            duplicates during induction. Ignored when ``embedding`` is None.
        max_facts / max_tips: Hard caps on bank size (highest-count kept).
        top_k_facts / top_k_tips: How many rules to inject per task when a
            retrieval embedding provider is configured.
        traj_char_budget: Max chars of trajectory text fed to the induce prompt.
        inject_enabled: Inject the bank (or top-K) into the system prompt.
        evolve_enabled: Run induction after each task to grow the bank.
        success_threshold: Score >= this counts as success (Slice 3 gating).
        induce_llm_policy: LLM invocation policy for induce/blame/synthesize.
        batch_size: Cost-amortization knob. When > 1, per-task observations are
            buffered and induced together via ONE ``induce_batch`` LLM call every
            ``batch_size`` tasks (blame/retire still run per failed task). 1 =
            induce on every task (default, the reference's per-task mode).
        batch_traj_budget: Per-task trajectory chars kept in the batch buffer
            (each task's excerpt is capped before the single batch induce call).
    """

    store_path: str = ".ttse/bank.json"
    embedding: Optional[EmbeddingProvider] = None
    dedup_threshold: float = 0.88
    max_facts: int = 40
    max_tips: int = 40
    top_k_facts: int = 10
    top_k_tips: int = 10
    traj_char_budget: int = 9000
    inject_enabled: bool = True
    evolve_enabled: bool = True
    success_threshold: float = 0.999
    induce_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY
    batch_size: int = 1
    batch_traj_budget: int = 1100


__all__ = ["TTSEConfig"]
