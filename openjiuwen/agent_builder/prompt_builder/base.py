#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import Generator, Optional

from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.prompt.template.template import Template
from openjiuwen.core.utils.llm.model_utils.model_factory import ModelFactory
from openjiuwen.core.component.common.configs.model_config import ModelConfig


class BasePromptBuilder(ABC):
    def __init__(self, model_config: ModelConfig):
        self._model: BaseModelClient = ModelFactory().get_model(
            model_provider=model_config.model_provider,
            api_base=model_config.model_info.api_base,
            api_key=model_config.model_info.api_key
        )
        self._model_name: str = model_config.model_info.model_name

    @abstractmethod
    def build(self,
              prompt: str | Template,
              **kwargs
              ) -> Optional[str]:
        raise NotImplementedError()

    @abstractmethod
    def stream_build(self,
                     prompt: str | Template,
                     **kwargs
                     ) -> Generator:
        raise NotImplementedError()