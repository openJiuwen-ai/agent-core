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
        inject_mode: Where FACT/TIP land. Default ``legacy_system`` keeps the
            current P:45 body (no behavior change). ``disk_catalog`` leaves a
            fixed guidance section, trails the category listing as a prompt
            attachment, and exposes ``ttse_consult(category=)`` for FACT/TIP.
            ``trailing_attach`` is accepted but currently falls back to
            ``legacy_system``.
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
    inject_mode: str = "legacy_system"
    evolve_enabled: bool = True
    success_threshold: float = 0.999
    induce_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY
    batch_size: int = 1
    batch_traj_budget: int = 1100
    consult_max_chars: int = 8000
    consult_max_rules: int = 40

    def is_disk_catalog(self) -> bool:
        """True when FACT/TIP are disclosed via ``ttse_consult``, not P:45.

        The category listing is trailed as a prompt attachment; P:45 is guidance only.
        """
        return str(self.inject_mode or "").strip() == "disk_catalog"


__all__ = ["TTSEConfig"]
