# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Epoch-level callback bridging Trainer (sync) to SkillDocumentOptimizer (async).

Triggers slow_update + meta_skill at epoch boundaries via asyncio.run().
Assumes Trainer is sync (no running event loop when callbacks fire).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, List

from openjiuwen.agent_evolving.trainer.progress import Callbacks, Progress

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.dataset import EvaluatedCase
    from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import SkillDocumentOptimizer
    from openjiuwen.core.single_agent import BaseAgent


class SkillDocumentCallbacks(Callbacks):
    """Epoch-level hooks for SkillDocumentOptimizer.

    Triggers slow_update + meta_skill at epoch boundaries.
    Uses asyncio.run() which assumes no running event loop (Trainer is sync).
    If Trainer becomes async in the future, this callback interface must also change.
    """

    def __init__(self, optimizer: "SkillDocumentOptimizer"):
        self._optimizer = optimizer

    def on_train_epoch_end(
        self,
        agent: "BaseAgent",
        progress: Progress,
        eval_info: List["EvaluatedCase"],
    ) -> None:
        """Bridge sync Trainer callback to async optimizer.run_epoch_end()."""
        asyncio.run(
            self._optimizer.run_epoch_end(
                epoch=progress.current_epoch,
                val_results=eval_info,
            )
        )
