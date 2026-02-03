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

from openjiuwen.core.common.logging import logger


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
        logger.debug(f"Getting graph state for session {session_id}, ns {ns}")
        try:
            state = await self._saver.get(session_id, ns)
            if state is None:
                logger.debug(f"Graph state not found for session {session_id}, ns {ns}")
            else:
                logger.debug(f"Successfully retrieved graph state for session {session_id}, ns {ns}, step {state.step}")
            return state
        except Exception as e:
            logger.error(f"Failed to get graph state for session {session_id}, ns {ns}: {e}")
            raise

    async def save(self, session_id: str, ns: str, state: GraphState) -> None:
        """Save graph state to storage."""
        logger.debug(f"Saving graph state for session {session_id}, ns {ns}, step {state.step}")
        try:
            await self._saver.save(session_id, ns, state)
            logger.debug(f"Successfully saved graph state for session {session_id}, ns {ns}, step {state.step}")
        except Exception as e:
            logger.error(f"Failed to save graph state for session {session_id}, ns {ns}, step {state.step}: {e}")
            raise

    async def delete(self, session_id: str, ns: Optional[str] = None) -> None:
        """Delete graph state from storage."""
        if ns is None:
            logger.debug(f"Deleting all graph states for session {session_id}")
        else:
            logger.debug(f"Deleting graph state for session {session_id}, ns {ns}")
        try:
            await self._saver.delete(session_id, ns)
            if ns is None:
                logger.debug(f"Successfully deleted all graph states for session {session_id}")
            else:
                logger.debug(f"Successfully deleted graph state for session {session_id}, ns {ns}")
        except Exception as e:
            if ns is None:
                logger.error(f"Failed to delete all graph states for session {session_id}: {e}")
            else:
                logger.error(f"Failed to delete graph state for session {session_id}, ns {ns}: {e}")
            raise
