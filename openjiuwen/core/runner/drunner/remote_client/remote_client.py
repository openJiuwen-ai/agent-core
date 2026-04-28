# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC
from typing import AsyncGenerator

from typing import Dict


class RemoteClient(ABC):
    async def start(self):
        pass

    async def stop(self):
        pass

    def is_started(self) -> bool:
        pass

    def is_stopped(self) -> bool:
        return not self.is_started()

    async def invoke(self, inputs: Dict, timeout: float = None) -> Dict:
        pass

    async def stream(self, inputs: dict, timeout: float = None) -> AsyncGenerator:
        pass
