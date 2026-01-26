# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional
from openjiuwen.core.session.checkpointer.base import Checkpointer

_default_inmemory_checkpointer: Optional[Checkpointer] = None


def get_default_inmemory_checkpointer() -> Checkpointer:
    global _default_inmemory_checkpointer

    if _default_inmemory_checkpointer is None:
        from openjiuwen.core.session.checkpointer.checkpointer import InMemoryCheckpointer
        _default_inmemory_checkpointer = InMemoryCheckpointer()

    return _default_inmemory_checkpointer
