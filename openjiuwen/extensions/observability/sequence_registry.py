# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-wide registry of the sequences a restated attribute refers to.

The GenAI convention has every model call restate its whole input, so an
attribute attached as its own full JSON string makes every span carry a copy
of the conversation -- measured at hundreds of kilobytes per chat span, which
is what fills exporter queues until the process dies. Attaching the sequence
*reference* instead and holding the content once here turns that quadratic
restatement into one copy per distinct element.

The registry keeps one entry per *chain prefix* (a sequence that grows reuses
every node it already has) and one entry per *distinct element* (identical
content is stored however many chains state it). A growing conversation
therefore costs its increment here: the same prefix sharing
:mod:`content_addressing` documents for its chains.

Lookup entries expire after the retention TTL. A discoverable descendant
keeps its predecessors charged even if those predecessors are no longer
individually discoverable. A consumer holding an acquired head can read its
chain after eviction; the registry charges only the chains it retains.
Every consumer of a reference drains in seconds -- export
batches, the storage sink -- while the TTL is hours, so an in-flight reference
always finds its content; a reference that outlives its content (a consumer
stalled for hours) states itself with a warning rather than failing the
encode. The byte cap stays as the fuse for content no bug may pin: crossing
it falls back to stating the full value inline, the pre-registry behavior.

The registry holds process state and therefore deliberately lives outside
:mod:`content_addressing`, which must stay stateless: the chain of a
sequence depends only on its content, whatever order spans are encoded in
and whichever thread encodes them.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from openjiuwen.core.common.logging import logger
from openjiuwen.extensions.observability.content_addressing import (
    AddressedSequence,
    MessageBody,
    SequenceHead,
    SequenceNode,
    chain_hash,
    content_hash,
    parse_sequence_reference,
)

# Sized against a measured heavy load: a seven-subject team restating
# ~150-190K-token contexts accumulated ~6-7 MB of distinct element content
# per hour (6,990 elements / 22 MB over a 3.5 h session). Eviction holds the
# steady state near the live working set, so the cap is only a fuse for
# content a bug pins in place; reaching it degrades to inline attributes
# rather than failing.
_DEFAULT_MAX_BYTES = 512 * 1024 * 1024
_ENV_MAX_BYTES = "OPENJIUWEN_SEQUENCE_REGISTRY_MAX_BYTES"
# Charge a flat per-node allowance for a sequence head and its index so a
# long conversation of tiny elements still counts its chains against the cap.
_NODE_BYTES = 160
# Content nothing has touched for this long is reclaimed. Deliberately not
# environment-tunable: consumers drain in seconds-to-minutes, so a low value
# turns an ordinary slow pipeline into degraded output. Two hours leaves two
# orders of margin over the slowest consumer (a stalled export batch).
_TTL_SECONDS = 2 * 60 * 60
# A sweep runs at most this often and only interleaved with registration, so
# its scan stays amortized against the work that feeds the registry.
_SWEEP_MIN_INTERVAL_SECONDS = 60
_SWEEP_EVERY_REGISTERS = 256

# Injectable for tests; production reads the monotonic clock.
_clock = time.monotonic


def _resolve_default_cap() -> int:
    """Read the byte cap from the environment, falling back to the default."""
    raw = os.environ.get(_ENV_MAX_BYTES, "").strip()
    if not raw:
        return _DEFAULT_MAX_BYTES
    try:
        cap = int(raw)
    except ValueError:
        return _DEFAULT_MAX_BYTES
    return cap if cap > 0 else _DEFAULT_MAX_BYTES


@dataclass(slots=True)
class _RegisteredNode:
    head: SequenceHead
    touched: float | None  # None means retained as an ancestor, but not individually discoverable.


class _SequenceRegistry:
    """Thread-safe, TTL-indexed sequences with registry-reachable byte accounting."""

    __slots__ = (
        "_blobs",
        "_last_sweep_at",
        "_lock",
        "_max_bytes",
        "_nodes",
        "_registers_since_sweep",
        "_stored_bytes",
        "_warned",
    )

    def __init__(self, max_bytes: int) -> None:
        self._lock = threading.Lock()
        self._blobs: dict[str, MessageBody] = {}
        self._nodes: dict[str, _RegisteredNode] = {}
        self._max_bytes = max(0, int(max_bytes))
        self._stored_bytes = 0
        self._warned = False
        self._registers_since_sweep = 0
        self._last_sweep_at = _clock()

    def register(self, sequence: AddressedSequence) -> bool:
        """Hold one sequence's nodes and content; report whether it may be stated.

        Registration refreshes the last-touched stamp of every node and
        element the sequence states, held or new, so restating a window
        keeps it resident. A sweep for content past the TTL runs before the
        cap check, which lets reclamation turn a full registry usable again.

        Args:
            sequence: The addressed sequence the attribute value became.

        Returns:
            ``True`` when the reference may be stated -- the content is
            already held, or has just been added. ``False`` when adding what
            it lacks would cross the byte cap (the caller then states the
            full value inline, the pre-registry behavior) or when the
            sequence is inconsistent or missing content.
        """
        with self._lock:
            now = _clock()
            self._registers_since_sweep += 1
            if (
                self._registers_since_sweep >= _SWEEP_EVERY_REGISTERS
                and now - self._last_sweep_at >= _SWEEP_MIN_INTERVAL_SECONDS
            ):
                self._evict_expired_locked(now)
            nodes = sequence.nodes
            if not nodes or sequence.depth != len(nodes) or sequence.seq_hash != nodes[-1].seq_hash:
                return False

            existing: list[_RegisteredNode] = []
            pending_nodes: list[SequenceNode] = []
            pending_blobs: dict[str, bytes] = {}
            previous = None
            for depth, node in enumerate(nodes, start=1):
                if node.prev_hash != previous or node.depth != depth:
                    return False
                content = sequence.blobs.get(node.blob_hash)
                if not isinstance(content, bytes):
                    return False
                held = self._nodes.get(node.seq_hash)
                if held is not None:
                    head = held.head
                    if (
                        head.depth != depth
                        or head.message.blob_hash != node.blob_hash
                        or (head.previous.seq_hash if head.previous is not None else None) != previous
                        or head.message.content != content
                    ):
                        return False
                    existing.append(held)
                else:
                    body = self._blobs.get(node.blob_hash)
                    if body is not None:
                        if body.content != content:
                            return False
                    elif node.blob_hash in pending_blobs:
                        if pending_blobs[node.blob_hash] != content:
                            return False
                    else:
                        if content_hash(content) != node.blob_hash:
                            return False
                        pending_blobs[node.blob_hash] = content
                    if chain_hash(previous, node.blob_hash) != node.seq_hash:
                        return False
                    pending_nodes.append(node)
                previous = node.seq_hash

            added_bytes = sum(len(content) for content in pending_blobs.values())
            added_bytes += _NODE_BYTES * len(pending_nodes)
            if self._stored_bytes + added_bytes > self._max_bytes:
                if not self._warned:
                    self._warned = True
                    logger.warning(
                        "sequence registry byte cap reached ({} bytes); "
                        "restated attributes fall back to inline until a sweep reclaims",
                        self._max_bytes,
                    )
                return False

            for blob_hash, content in pending_blobs.items():
                self._blobs[blob_hash] = MessageBody(blob_hash, content)
            head = existing[-1].head if existing else None
            for node in pending_nodes:
                head = SequenceHead(node.seq_hash, node.depth, self._blobs[node.blob_hash], head)
                self._nodes[node.seq_hash] = _RegisteredNode(head, now)
            for entry in existing:
                entry.touched = now
            self._stored_bytes += added_bytes
            return True

    def sequence_head(self, seq_hash: str) -> SequenceHead | None:
        """Return a discoverable head and refresh only that entry in constant time."""
        with self._lock:
            entry = self._nodes.get(seq_hash)
            if entry is None or entry.touched is None:
                return None
            entry.touched = _clock()
            return entry.head

    def sequence(self, seq_hash: str) -> AddressedSequence | None:
        """Rebuild the storage encoder's view and refresh the complete chain."""
        with self._lock:
            entry = self._nodes.get(seq_hash)
            if entry is None or entry.touched is None:
                return None
            now = _clock()
            nodes: list[SequenceNode] = []
            blobs: dict[str, bytes] = {}
            head = entry.head
            node: SequenceHead | None = head
            while node is not None:
                previous = node.previous
                body = node.message
                self._nodes[node.seq_hash].touched = now
                nodes.append(SequenceNode(node.seq_hash, previous.seq_hash if previous else None,
                                          body.blob_hash, node.depth))
                blobs[body.blob_hash] = body.content
                node = previous
            nodes.reverse()
            return AddressedSequence(
                key="",
                seq_hash=head.seq_hash,
                depth=head.depth,
                logical_bytes=sum(len(content) for content in blobs.values()),
                nodes=tuple(nodes),
                blobs=blobs,
            )

    def _evict_expired_locked(self, now: float) -> None:
        """Expire discoverability, then release only unreachable nodes and bodies."""
        self._last_sweep_at = now
        self._registers_since_sweep = 0
        deadline = now - _TTL_SECONDS
        reachable: set[str] = set()
        live_blobs: set[str] = set()
        for entry in self._nodes.values():
            if entry.touched is None or entry.touched < deadline:
                entry.touched = None
                continue
            node: SequenceHead | None = entry.head
            while node is not None and node.seq_hash not in reachable:
                reachable.add(node.seq_hash)
                live_blobs.add(node.message.blob_hash)
                node = node.previous

        dead_nodes = self._nodes.keys() - reachable
        for seq_hash in dead_nodes:
            del self._nodes[seq_hash]
        self._stored_bytes -= _NODE_BYTES * len(dead_nodes)

        dead_blobs = self._blobs.keys() - live_blobs
        for blob_hash in dead_blobs:
            self._stored_bytes -= len(self._blobs.pop(blob_hash).content)

        reclaimed = len(dead_nodes) + len(dead_blobs)
        if reclaimed:
            logger.info("sequence registry reclaimed {} entries past the {}s TTL", reclaimed, _TTL_SECONDS)
            self._warned = False

    def materialize(self, seq_hash: str) -> str | None:
        """Refresh the full discoverable sequence and rebuild it outside the lock."""
        with self._lock:
            entry = self._nodes.get(seq_hash)
            if entry is None or entry.touched is None:
                return None
            head = entry.head
            now = _clock()
            node: SequenceHead | None = head
            while node is not None:
                self._nodes[node.seq_hash].touched = now
                node = node.previous
        return head.materialize()

    def stats(self) -> tuple[int, int]:
        """Return the held element count and the charged byte total."""
        with self._lock:
            return len(self._blobs), self._stored_bytes


_lock = threading.Lock()
_registry: _SequenceRegistry | None = None


def _get_registry() -> _SequenceRegistry:
    """Return the process-wide registry, creating it on first use."""
    global _registry
    with _lock:
        if _registry is None:
            _registry = _SequenceRegistry(_resolve_default_cap())
        return _registry


def register_sequence(sequence: AddressedSequence) -> bool:
    """Hold one sequence's nodes and content so a reference to it may be stated.

    See :meth:`_SequenceRegistry.register` for the return contract.
    """
    return _get_registry().register(sequence)


def lookup_sequence_head(seq_hash: str) -> SequenceHead | None:
    """Acquire an immutable head, refreshing only its lookup entry.

    A held head remains readable after registry eviction or reset.
    """
    return _get_registry().sequence_head(seq_hash)


def lookup_sequence(seq_hash: str) -> AddressedSequence | None:
    """Return the addressed sequence a hash names, or ``None`` when unknown."""
    return _get_registry().sequence(seq_hash)


def unresolvable_placeholder(seq_hash: str, depth: int) -> str:
    """The value stated wherever a reference names content the registry lost.

    One shape for every consumer -- readable encode, storage encode,
    exporter-boundary transforms -- so a lost window reads as a declared
    loss instead of a reference that parses yet never resolves.
    """
    return (
        f'{{"unresolvable_sequence": "{seq_hash}", '
        f'"depth": {depth}, "reason": "registry_evicted"}}'
    )


def materialize_stated_value(value: str) -> str:
    """Return the full attribute value a stated reference (or plain value) names.

    A reference the registry no longer holds -- evicted after its TTL, with
    no consumer having touched it for hours -- is stated as an explicit
    placeholder naming the loss, the same marker the storage encode writes:
    every consumer of a lost reference sees one declared shape instead of a
    reference that parses yet never resolves.
    """
    parsed = parse_sequence_reference(value)
    if parsed is None:
        return value
    seq_hash, depth = parsed
    materialized = _get_registry().materialize(seq_hash)
    if materialized is None:
        logger.warning("sequence reference {} is not registered; stating an unresolvable placeholder", seq_hash)
        return unresolvable_placeholder(seq_hash, depth)
    return materialized


def registry_stats() -> tuple[int, int]:
    """Return how many distinct elements the registry holds, and its byte total."""
    return _get_registry().stats()


def reset_registry(max_bytes: int | None = None) -> None:
    """Drop every held sequence and re-arm the cap; for tests and restarts."""
    global _registry
    with _lock:
        _registry = _SequenceRegistry(max_bytes if max_bytes is not None else _resolve_default_cap())
