#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Self, Union, Any, AsyncIterator, Hashable, Callable, Awaitable

from openjiuwen.core.common.constants.constant import INPUTS_KEY, CONFIG_KEY
from openjiuwen.core.graph.executable import Executable, Output, Input
from openjiuwen.core.runtime.runtime import BaseRuntime


class ExecutableGraph(Executable[Input, Output]):
    async def invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        return await self._invoke(inputs.get(INPUTS_KEY), runtime, inputs.get(CONFIG_KEY))

    async def stream(self, inputs: Input, runtime: BaseRuntime) -> AsyncIterator[Output]:
        pass

    async def collect(self, inputs: AsyncIterator[Input], contex: BaseRuntime) -> Output:
        pass

    async def transform(self, inputs: AsyncIterator[Input], runtime: BaseRuntime) -> AsyncIterator[Output]:
        pass

    async def interrupt(self, message: dict):
        pass

    @abstractmethod
    async def _invoke(self, inputs: Input, runtime: BaseRuntime, config: Any = None) -> Output:
        pass


Router = Union[
    Callable[..., Union[Hashable, list[Hashable]]],
    Callable[..., Awaitable[Union[Hashable, list[Hashable]]]],
]


class Graph(ABC):
    def start_node(self, node_id: str) -> Self:
        pass

    def end_node(self, node_id: str) -> Self:
        pass

    def add_node(self, node_id: str, node: Executable, *, wait_for_all: bool = False) -> Self:
        pass

    def add_edge(self, source_node_id: Union[str, list[str]], target_node_id: str) -> Self:
        pass

    def add_conditional_edges(self, source_node_id: str, router: Any) -> Self:
        pass

    def compile(self, runtime: BaseRuntime) -> ExecutableGraph:
        pass

    def get_nodes(self) -> dict:
        pass