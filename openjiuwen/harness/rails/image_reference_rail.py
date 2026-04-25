# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that enables image-reference context processing."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openjiuwen.core.context_engine import ImageReferenceProcessorConfig
from openjiuwen.harness.rails.base import DeepAgentRail


_PROCESSOR_KEY = "ImageReferenceProcessor"


class ImageReferenceRail(DeepAgentRail):
    """Install the image-reference processor on the inner ReAct agent.

    The rail is intentionally thin: the processor performs the temporary
    multimodal context-window transformation, while this rail only makes the
    behavior easy to attach/detach per agent.
    """

    priority = 84

    def __init__(self, config: ImageReferenceProcessorConfig | dict[str, Any] | None = None):
        super().__init__()
        if isinstance(config, dict):
            self._config = ImageReferenceProcessorConfig(**config)
        elif config is None:
            self._config = ImageReferenceProcessorConfig()
        else:
            self._config = config

    @property
    def config(self) -> ImageReferenceProcessorConfig:
        return self._config

    def init(self, agent) -> None:
        react_config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if react_config is None:
            return
        react_config.context_processors = self._merge_processor(
            list(react_config.context_processors or []),
            self._config,
        )

    def uninit(self, agent) -> None:
        react_config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if react_config is None:
            return
        react_config.context_processors = [
            item for item in (react_config.context_processors or [])
            if item[0] != _PROCESSOR_KEY
        ]

    @staticmethod
    def _merge_processor(
            processors: list[tuple[str, BaseModel]],
            config: ImageReferenceProcessorConfig,
    ) -> list[tuple[str, BaseModel]]:
        merged: list[tuple[str, BaseModel]] = []
        replaced = False
        for key, existing_config in processors:
            if key == _PROCESSOR_KEY:
                merged.append((key, config))
                replaced = True
            else:
                merged.append((key, existing_config))
        if not replaced:
            merged.append((_PROCESSOR_KEY, config))
        return merged
