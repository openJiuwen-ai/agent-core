# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-configured persistence for extension package data."""

from openjiuwen.core.extension.data.store import JsonStore
from openjiuwen.core.extension.data.state import JsonState
from openjiuwen.core.extension.data.dependencies import StateChanges, StateDependencies
from openjiuwen.core.extension.data.state_models import StateAddress, StateRef, StateSetResult, StateSnapshot

__all__ = [
    "JsonStore", "JsonState", "StateRef", "StateAddress", "StateSnapshot", "StateSetResult",
    "StateDependencies", "StateChanges",
]
