# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded, in-memory receipts for input to an existing execution.

All methods are synchronous and owned by one event loop. Hosts serialize
admission with their existing control lock/supervisor; the inner loop closes
the acceptance window without yielding before it commits its final answer.
Receipts describe context admission, not model compliance or durable delivery.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict


class SteeringInput(str):
    """A backwards-compatible text value carrying a context-admission receipt."""

    def __new__(cls, content: str, inbox: "SteeringInbox", key: tuple[str, str]):
        value = super().__new__(cls, content)
        value.inbox = inbox
        value.key = key
        return value

    @property
    def input_id(self) -> str:
        return self.key[1]

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def begin_context_write(self) -> None:
        self.inbox.begin_context_write(self.key)

    def settle(self, status: str, reason: str | None = None) -> None:
        self.inbox.settle(self.key, status, reason)


class SteeringWindow:
    """One runtime admission window; late callbacks cannot close a newer one."""

    def __init__(self, inbox: "SteeringInbox") -> None:
        self.inbox = inbox

    @property
    def current(self) -> bool:
        return self.inbox.window is self

    def close_acceptance(self) -> None:
        if self.current:
            self.inbox.close_acceptance()

    def finish(self, reason: str = "execution_finished") -> None:
        if self.current:
            self.inbox.finish(reason)


class SteeringInbox:
    """Atomic queue admission and bounded idempotency for active-only inputs."""

    def __init__(self, *, max_pending: int = 128, max_records: int = 512) -> None:
        self.max_pending = max_pending
        self.max_records = max_records
        self.owner: str | None = None
        self.accepting = False
        self.queue: asyncio.Queue | None = None
        self.window: SteeringWindow | None = None
        self._records: OrderedDict[tuple[str, str], dict] = OrderedDict()

    @staticmethod
    def result(owner: str, input_id: str, status: str, reason: str | None = None) -> dict:
        result = {"active_request_id": owner, "input_id": input_id, "status": status}
        if reason:
            result["reason"] = reason
        return result

    def begin_round(self, owner: str) -> SteeringWindow:
        """Start an in-memory admission window for one execution round."""
        self.finish("execution_finished")
        self.owner = owner
        self.queue = None
        self.accepting = True
        self.window = SteeringWindow(self)
        return self.window

    def bind(self, queue: asyncio.Queue) -> SteeringWindow | None:
        self.queue = queue
        queue.steering_inbox = self.window
        return self.window

    def lookup(self, owner: str, input_id: str) -> dict:
        record = self._records.get((owner, input_id))
        return dict(record["result"]) if record else self.result(owner, input_id, "unknown", "unknown_input")

    def previous(self, owner: str, input_id: str, content: str) -> dict | None:
        record = self._records.get((owner, input_id))
        if record is None:
            return None
        if record["content"] != content:
            return self.result(owner, input_id, "not_applied", "input_id_conflict")
        return dict(record["result"])

    def accept(self, owner: str, input_id: str, content: str) -> dict:
        invalid_content = not isinstance(content, str) or not content.strip() or len(content) > 32000
        if invalid_content or not input_id:
            return self.result(owner, input_id, "not_applied", "invalid_input")
        previous = self.previous(owner, input_id, content)
        if previous is not None:
            return previous
        if self.owner != owner or not self.accepting or self.queue is None:
            return self.result(owner, input_id, "not_applied", "not_active")
        pending = sum(r["result"]["status"] == "accepted" for r in self._records.values())
        if pending >= self.max_pending or self.queue.full():
            return self.result(owner, input_id, "not_applied", "queue_full")
        if len(self._records) >= self.max_records:
            # Never evict this execution's dedup keys: a retry must not enqueue twice.
            evict = next(
                (
                    key
                    for key, record in self._records.items()
                    if key[0] != owner and record["result"]["status"] != "accepted"
                ),
                None,
            )
            if evict is None:
                return self.result(owner, input_id, "not_applied", "queue_full")
            del self._records[evict]
        key = (owner, input_id)
        result = self.result(owner, input_id, "accepted")
        # No await between dedup registration and enqueue, nor between final
        # empty check and close_acceptance in the consumer.
        self.queue.put_nowait(SteeringInput(content, self, key))
        self._records[key] = {"content": content, "result": result}
        return dict(result)

    def begin_context_write(self, key: tuple[str, str]) -> None:
        """Track an in-flight context write without exposing receipt storage."""
        record = self._records.get(key)
        if record is not None:
            record["writing"] = True

    def settle(self, key: tuple[str, str], status: str, reason: str | None = None) -> None:
        record = self._records.get(key)
        if record is not None and record["result"]["status"] == "accepted":
            record["result"] = self.result(*key, status, reason)

    def close_acceptance(self) -> None:
        self.accepting = False

    def finish(self, reason: str = "execution_finished") -> None:
        self.close_acceptance()
        for key, record in self._records.items():
            if key[0] == self.owner and record["result"]["status"] == "accepted":
                self.settle(key, "unknown" if record.get("writing") else "not_applied", reason)
        if self.queue is not None:
            retained = []
            while not self.queue.empty():
                value = self.queue.get_nowait()
                if not isinstance(value, SteeringInput) or value.inbox is not self:
                    retained.append(value)
            for value in retained:
                self.queue.put_nowait(value)
