# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import (
    Any,
    List,
    Dict,
    Optional
)

from openjiuwen.core.runtime.runtime import BaseRuntime


@dataclass
class PendingNode:
    node_name: str
    status: str
    exception: list[Exception] = None


@dataclass
class GraphState:
    ns: str
    step: int
    channel_values: Dict[str, Any]
    pending_buffer: List["Message"]
    pending_node: Dict[str, PendingNode]
    node_version: Dict[str, int]


class Store(ABC):
    @abstractmethod
    async def get(self, session_id: str, ns: str) -> Optional[GraphState]:
        ...

    @abstractmethod
    async def save(self, session_id: str, ns: str, state: GraphState) -> None:
        ...

    @abstractmethod
    async def delete(self, session_id: str, ns: Optional[str] = None) -> None:
        ...


def create_state(
        ns: str,
        step: int,
        channel_snapshot: Dict[str, Any],
        *,
        pending_buffer: Optional[List["Message"]] = None,
        pending_node: Optional[Dict[str, PendingNode]] = None,
        node_version: Dict[str, int] = None

) -> GraphState:
    return GraphState(
        ns=ns,
        step=step,
        channel_values=channel_snapshot,
        pending_buffer=pending_buffer or [],
        pending_node=pending_node or {},
        node_version=node_version or {},
    )


class GraphStore(Store):
    def __init__(self, runtime: BaseRuntime, saver: Store):
        self._saver = saver
        self.ctx = runtime

    def reset(self, runtime: BaseRuntime):
        self.ctx = runtime

    async def get(self, session_id: str, ns: str) -> Optional[GraphState]:
        return await self._saver.get(session_id, ns)

    async def save(self, session_id: str, ns: str, state: GraphState) -> None:
        return await self._saver.save(session_id, ns, state)

    async def delete(self, session_id: str, ns: Optional[str] = None) -> None:
        return await self._saver.delete(session_id, ns)
