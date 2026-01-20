# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod

from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.session import BaseSession


class Storage(ABC):
    @abstractmethod
    def save(self, session: BaseSession):
        pass

    @abstractmethod
    def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        pass

    @abstractmethod
    def clear(self, session_id: str):
        pass

    @abstractmethod
    def exists(self, session: BaseSession) -> bool:
        pass
