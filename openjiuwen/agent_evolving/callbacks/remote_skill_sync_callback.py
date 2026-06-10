# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Push skill content to remote rollout agents after each epoch.

When the rollout Agent runs on a remote machine, the local Trainer's
candidate validation updates the in-memory operator state, but the remote
agent's skill file may not be synced. This callback POSTs the authoritative
skill_content to a remote endpoint after each epoch.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, List

import requests

from openjiuwen.agent_evolving.trainer.progress import Callbacks
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.dataset import EvaluatedCase
    from openjiuwen.agent_evolving.trainer.progress import Progress
    from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator
    from openjiuwen.core.single_agent import BaseAgent


class RemoteSkillSyncCallback(Callbacks):
    """Push latest skill content from local operator to remote rollout Agent.

    Injection point: on_train_epoch_end (after Trainer candidate validation).
    """

    def __init__(
        self,
        sync_endpoint: str,
        skill_name: str,
        content_provider: Callable[[], str],
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        self._sync_endpoint = sync_endpoint
        self._skill_name = skill_name
        self._content_provider = content_provider
        self._timeout = timeout
        self._max_retries = max_retries
        self._last_pushed_epoch: int | str = -1

    @classmethod
    def from_operator(
        cls,
        *,
        sync_endpoint: str,
        skill_name: str,
        operator: "SkillDocumentOperator",
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> "RemoteSkillSyncCallback":
        """Build callback that reads skill content from an operator."""
        from openjiuwen.agent_evolving.protocols import SKILL_CONTENT_TARGET

        return cls(
            sync_endpoint=sync_endpoint,
            skill_name=skill_name,
            content_provider=lambda: operator.get_state().get(SKILL_CONTENT_TARGET, ""),
            timeout=timeout,
            max_retries=max_retries,
        )

    def on_train_epoch_end(
        self,
        agent: "BaseAgent",
        progress: "Progress",
        eval_info: List["EvaluatedCase"],
    ) -> None:
        """Push skill content after each epoch, avoiding duplicate pushes."""
        if progress.current_epoch == self._last_pushed_epoch:
            return

        self._post_with_retry(
            {
                "skill_name": self._skill_name,
                "content": self._content_provider(),
                "epoch": progress.current_epoch,
                "score": progress.best_score,
            }
        )
        self._last_pushed_epoch = progress.current_epoch

    def on_train_end(
        self,
        agent: "BaseAgent",
        progress: "Progress",
        eval_info: List["EvaluatedCase"],
    ) -> None:
        """Push final skill content when training ends."""
        self._post_with_retry(
            {
                "skill_name": self._skill_name,
                "content": self._content_provider(),
                "epoch": "final",
                "score": progress.best_score,
            }
        )

    def _post_with_retry(self, payload: dict) -> None:
        """POST with exponential backoff retry."""
        for attempt in range(self._max_retries + 1):
            try:
                resp = requests.post(self._sync_endpoint, json=payload, timeout=self._timeout)
                resp.raise_for_status()
                return
            except requests.RequestException as e:
                if attempt == self._max_retries:
                    logger.warning(
                        "RemoteSkillSync: failed to push skill epoch=%s after %s attempts: %s",
                        payload.get("epoch"),
                        self._max_retries + 1,
                        e,
                    )
                else:
                    time.sleep(2**attempt)
