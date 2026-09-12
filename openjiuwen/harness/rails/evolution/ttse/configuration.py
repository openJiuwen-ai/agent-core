# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configure / unconfigure API for the TTSE rail.

Attaches a :class:`TTSERail` (FACT/TIP dual-track) to an agent. Because
``TTSERail`` now inherits :class:`EvolutionRail` rather than
:class:`SkillEvolutionRail`, it can coexist with a skill-evolution rail on
the same agent — call :func:`configure_skill_evolution` separately if the
skill-body track is also wanted.
"""

from __future__ import annotations

from typing import Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

from .config import TTSEConfig
from .success import SuccessDetector
from .ttse_rail import TTSERail


def _find_existing_ttse_rail(agent) -> Optional[TTSERail]:
    """Return an existing TTSERail on ``agent`` (exact class), if any."""
    rails = agent.find_rails_by_type((TTSERail,))
    for rail in rails:
        if isinstance(rail, TTSERail):
            return rail
    return None


def configure_ttse_evolution(
    agent,
    *,
    llm: Model,
    model: str,
    ttse_config: Optional[TTSEConfig] = None,
    embedding=None,
    success_detector: Optional[SuccessDetector] = None,
    **rail_kwargs,
):
    """Attach a :class:`TTSERail` to ``agent``.

    Idempotent: a no-op when a ``TTSERail`` is already present. The rail
    drives the FACT / meta-TIP tracks only.

    Args:
        agent: The agent to configure.
        llm: LLM client for induction / blame / synthesize.
        model: Model name for the TTSE LLM calls.
        ttse_config: Bank / retrieval / dedup knobs. Defaults to ``TTSEConfig()``.
        embedding: Optional embedding provider for semantic dedup, Auto-dream
            clustering, and ``ttse_consult`` hybrid recall.
        success_detector: Optional success signal gating the blame/synthesize pass.
        **rail_kwargs: Forwarded to :class:`EvolutionRail` (trajectory_store,
            evolution_trigger, async_evolution, ...).

    Returns:
        The agent, for chaining.
    """
    existing = _find_existing_ttse_rail(agent)
    if existing is not None:
        logger.info("[TTSERail] already mounted; skipping duplicate configure")
        return agent
    cfg = ttse_config or TTSEConfig()
    rail = TTSERail(
        llm=llm,
        model=model,
        ttse_config=cfg,
        embedding=embedding,
        success_detector=success_detector,
        **rail_kwargs,
    )
    agent.add_rail(rail)
    logger.info(
        "[TTSERail] mounted evolve_enabled=%s inject_enabled=%s batch_size=%s store_path=%s",
        cfg.evolve_enabled,
        cfg.inject_enabled,
        cfg.batch_size,
        cfg.store_path,
    )
    return agent


def unconfigure_ttse_evolution(agent) -> int:
    """Remove the TTSERail from ``agent``.

    Returns the number of rails removed.
    """
    removed = agent.strip_rails_by_type((TTSERail,))
    if removed:
        logger.info("[TTSERail] unmounted (%s rail(s) removed)", removed)
    return removed


__all__ = ["configure_ttse_evolution", "unconfigure_ttse_evolution"]
