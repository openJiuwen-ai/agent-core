# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from abc import (
    ABC,
    abstractmethod,
)
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from openjiuwen.core.common.logging import graph_logger, LogEventType


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
    def __init__(self, saver: Store):
        self._saver = saver

    async def get(self, session_id: str, ns: str) -> Optional[GraphState]:
        """Get graph state from storage."""
        graph_logger.debug(
            "Retrieving graph state",
            event_type=LogEventType.GRAPH_STORE_RETRIEVE,
            metadata={"session_id": session_id, "namespace": ns}
        )
        try:
            state = await self._saver.get(session_id, ns)
            if state is None:
                graph_logger.debug(
                    "Graph state not found",
                    event_type=LogEventType.GRAPH_STORE_RETRIEVE,
                    metadata={"session_id": session_id, "namespace": ns}
                )
            else:
                graph_logger.debug(
                    "Graph state retrieved successfully",
                    event_type=LogEventType.GRAPH_STORE_RETRIEVE,
                    metadata={"session_id": session_id, "namespace": ns, "step": state.step}
                )
            return state
        except Exception as e:
            graph_logger.error(
                "Failed to retrieve graph state",
                event_type=LogEventType.GRAPH_STORE_RETRIEVE,
                metadata={"session_id": session_id, "namespace": ns, "error": str(e)}
            )
            raise

    async def save(self, session_id: str, ns: str, state: GraphState) -> None:
        """Save graph state to storage."""
        graph_logger.debug(
            "Saving graph state",
            event_type=LogEventType.GRAPH_STORE_ADD,
            metadata={"session_id": session_id, "namespace": ns, "step": state.step}
        )
        try:
            await self._saver.save(session_id, ns, state)
            graph_logger.debug(
                "Graph state saved successfully",
                event_type=LogEventType.GRAPH_STORE_ADD,
                metadata={"session_id": session_id, "namespace": ns, "step": state.step}
            )
        except Exception as e:
            graph_logger.error(
                "Failed to save graph state",
                event_type=LogEventType.GRAPH_STORE_ADD,
                metadata={"session_id": session_id, "namespace": ns, "step": state.step, "error": str(e)}
            )
            raise

    async def delete(self, session_id: str, ns: Optional[str] = None) -> None:
        """Delete graph state from storage."""
        if ns is None:
            graph_logger.debug(
                "Deleting all graph states for session",
                event_type=LogEventType.GRAPH_STORE_DELETE,
                metadata={"session_id": session_id}
            )
        else:
            graph_logger.debug(
                "Deleting graph state",
                event_type=LogEventType.GRAPH_STORE_DELETE,
                metadata={"session_id": session_id, "namespace": ns}
            )
        try:
            await self._saver.delete(session_id, ns)
            if ns is None:
                graph_logger.debug(
                    "All graph states deleted successfully",
                    event_type=LogEventType.GRAPH_STORE_DELETE,
                    metadata={"session_id": session_id}
                )
            else:
                graph_logger.debug(
                    "Graph state deleted successfully",
                    event_type=LogEventType.GRAPH_STORE_DELETE,
                    metadata={"session_id": session_id, "namespace": ns}
                )
        except Exception as e:
            if ns is None:
                graph_logger.error(
                    "Failed to delete all graph states",
                    event_type=LogEventType.GRAPH_STORE_DELETE,
                    metadata={"session_id": session_id, "error": str(e)}
                )
            else:
                graph_logger.error(
                    "Failed to delete graph state",
                    event_type=LogEventType.GRAPH_STORE_DELETE,
                    metadata={"session_id": session_id, "namespace": ns, "error": str(e)}
                )
            raise
