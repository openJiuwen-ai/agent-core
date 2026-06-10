# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compose multiple Callbacks into a single callback.

Trainer only accepts a single Callbacks object. Use this class to combine
SkillDocumentCallbacks, RemoteSkillSyncCallback, and other callbacks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from openjiuwen.agent_evolving.trainer.progress import Callbacks
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.dataset import EvaluatedCase
    from openjiuwen.agent_evolving.trainer.progress import Progress
    from openjiuwen.core.single_agent import BaseAgent


class ComposedCallbacks(Callbacks):
    """Compose multiple Callbacks, calling each in registration order.

    Exceptions in one callback are caught and logged so subsequent
    callbacks still execute.
    """

    def __init__(self, *callbacks: Callbacks):
        self._callbacks = list(callbacks)

    def on_train_begin(self, agent: "BaseAgent", progress: "Progress", eval_info: List["EvaluatedCase"]) -> None:
        for cb in self._callbacks:
            try:
                cb.on_train_begin(agent, progress, eval_info)
            except Exception:
                logger.warning("ComposedCallbacks: on_train_begin failed for %s", type(cb).__name__, exc_info=True)

    def on_train_end(self, agent: "BaseAgent", progress: "Progress", eval_info: List["EvaluatedCase"]) -> None:
        for cb in self._callbacks:
            try:
                cb.on_train_end(agent, progress, eval_info)
            except Exception:
                logger.warning("ComposedCallbacks: on_train_end failed for %s", type(cb).__name__, exc_info=True)

    def on_train_epoch_begin(self, agent: "BaseAgent", progress: "Progress") -> None:
        for cb in self._callbacks:
            try:
                cb.on_train_epoch_begin(agent, progress)
            except Exception:
                logger.warning(
                    "ComposedCallbacks: on_train_epoch_begin failed for %s", type(cb).__name__, exc_info=True
                )

    def on_train_epoch_end(self, agent: "BaseAgent", progress: "Progress", eval_info: List["EvaluatedCase"]) -> None:
        for cb in self._callbacks:
            try:
                cb.on_train_epoch_end(agent, progress, eval_info)
            except Exception:
                logger.warning("ComposedCallbacks: on_train_epoch_end failed for %s", type(cb).__name__, exc_info=True)
