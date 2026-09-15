# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Sleep backend facade: protocol base + factory."""

from __future__ import annotations

from typing import Optional

from openjiuwen.agent_evolving.skill_train.llm_client import ChatLLMClient
from openjiuwen.agent_evolving.skill_train.sleep.backend_base import Backend
from openjiuwen.agent_evolving.skill_train.sleep.mock_backend import MockBackend
from openjiuwen.agent_evolving.skill_train.sleep.model_backend import ModelBackend
from openjiuwen.agent_evolving.skill_train.sleep.scoring import exact_score, keyword_soft_score

__all__ = [
    "Backend",
    "MockBackend",
    "ModelBackend",
    "build_backend",
    "exact_score",
    "keyword_soft_score",
]


def build_backend(
    name: str,
    *,
    target_client: Optional[ChatLLMClient] = None,
    optimizer_client: Optional[ChatLLMClient] = None,
    preferences: str = "",
) -> Backend:
    kind = (name or "mock").strip().lower()
    if kind == "mock":
        backend = MockBackend()
        backend.preferences = preferences
        return backend
    if kind == "model":
        if target_client is None:
            raise ValueError("ModelBackend requires target_client")
        return ModelBackend(
            target_client=target_client,
            optimizer_client=optimizer_client,
            preferences=preferences,
        )
    raise ValueError(f"unknown sleep backend {name!r}; expected mock|model")
