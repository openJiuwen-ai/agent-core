# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, List, Union, Protocol

from openjiuwen.core.graph.pregel.base import IRouter, Message, TriggerMessage, BarrierMessage
from openjiuwen.core.graph.pregel.task import _callable_traits


class StaticRouter(IRouter):
    """send to targets (1→N)."""

    def __init__(self, targets: List[str]):
        self.targets = targets

    async def dispatch(self, source_node: str) -> List[Message]:
        return [TriggerMessage(sender=source_node, target=to) for to in self.targets]


class SelectorProtocol(Protocol):
    def __call__(self, output: Any = None) -> Union[str, List[str]]:
        pass


class ConditionalRouter(IRouter):
    """Send to targets chosen by selector(output)."""

    def __init__(self, selector: SelectorProtocol):
        self.selector = selector
        # Reflection traits are constants of the selector: resolve them once
        # at build time instead of on every dispatch.
        traits = _callable_traits(selector)
        self._takes_state = traits.has_state
        self._is_coroutine = traits.is_coroutine

    async def dispatch(self, source_node: str) -> List[Message]:
        kwargs = {}
        if self._takes_state:
            kwargs["state"] = None

        if self._is_coroutine:
            targets = await self.selector(**kwargs)
        else:
            targets = self.selector(**kwargs)
        if isinstance(targets, str):
            targets = [targets]
        return [TriggerMessage(sender=source_node, target=to) for to in targets]


class BarrierRouter(IRouter):
    """Special route that sends a Signal + SenderID to a barrier"""

    def __init__(self, targets: list[str]):
        self.targets = targets

    async def dispatch(self, source_node: str) -> List[Message]:
        return [BarrierMessage(sender=source_node, target=to) for to in self.targets]
