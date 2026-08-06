from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class RetrieverCandidate:
    rank: int
    item_id: str
    payload: str
    branch_path: tuple[str, ...]
    label: str = ""
    description: str = ""


@dataclass(frozen=True)
class RetrieverItem:
    item_id: str
    payload: str
    label: str = ""
    description: str = ""


@dataclass(frozen=True)
class RetrieverChoice:
    choice_id: str
    payload: str
    description: str = ""


@dataclass(frozen=True)
class RetrieverNode:
    node_id: str
    label: str
    description: str = ""
    children: tuple["RetrieverNode", ...] = ()
    items: tuple[RetrieverItem, ...] = ()


@dataclass(frozen=True)
class RetrieverTraceEvent:
    event_type: str
    node_id: str
    depth: int
    detail: Dict[str, object] = field(default_factory=dict)


@dataclass
class RetrieverTrace:
    events: List[RetrieverTraceEvent] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, event_type: str, *, node_id: str, depth: int, detail: Dict[str, object] | None = None) -> None:
        payload = dict(detail or {})
        with self._lock:
            self.events.append(RetrieverTraceEvent(event_type=event_type, node_id=node_id, depth=depth, detail=payload))


__all__ = [
    "RetrieverCandidate",
    "RetrieverChoice",
    "RetrieverItem",
    "RetrieverNode",
    "RetrieverTrace",
    "RetrieverTraceEvent",
]
