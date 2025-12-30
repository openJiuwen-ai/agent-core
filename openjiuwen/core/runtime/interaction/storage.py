# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod

from openjiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from openjiuwen.core.runtime.runtime import BaseRuntime


class Storage(ABC):
    @abstractmethod
    def save(self, runtime: BaseRuntime):
        pass

    @abstractmethod
    def recover(self, runtime: BaseRuntime, inputs: InteractiveInput = None):
        pass

    @abstractmethod
    def clear(self, session_id: str):
        pass

    @abstractmethod
    def exists(self, runtime: BaseRuntime) -> bool:
        pass
