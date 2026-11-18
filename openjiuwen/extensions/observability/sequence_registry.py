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

Entries are reclaimed, not kept forever: content nothing has touched for the
retention TTL goes, so a finished session's history does not accumulate on a
long-lived worker. Every consumer of a reference drains in seconds -- export
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

from openjiuwen.core.common.logging import logger
from openjiuwen.extensions.observability.content_addressing import (
    AddressedSequence,
    SequenceNode,
    parse_sequence_reference,
    rebuild_value,
)

# Sized against a measured heavy load: a seven-subject team restating
# ~150-190K-token contexts accumulated ~6-7 MB of distinct element content
# per hour (6,990 elements / 22 MB over a 3.5 h session). Eviction holds the
# steady state near the live working set, so the cap is only a fuse for
# content a bug pins in place; reaching it degrades to inline attributes
# rather than failing.
_DEFAULT_MAX_BYTES = 512 * 1024 * 1024
_ENV_MAX_BYTES = "OPENJIUWEN_SEQUENCE_REGISTRY_MAX_BYTES"
# A node is three hashes and a depth; charge a flat per-node allowance so a
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


class _SequenceRegistry:
    """Thread-safe store of chain nodes and element content, TTL-reclaimed.

    Two maps: nodes by their chain-prefix hash (shared by every sequence
    extending them) and blobs by their content hash (shared by every node
    stating them). Both carry a last-touched stamp that registration and
    every chain walk refresh, so an entry ages out only when no consumer has
    reached it through any chain for the whole TTL.
    """

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
        self._blobs: dict[str, tuple[bytes, float]] = {}
        self._nodes: dict[str, tuple[str | None, str, int, float]] = {}
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
            sequence is malformed (a node without its element's content).
        """
        with self._lock:
            now = _clock()
            self._registers_since_sweep += 1
            if (
                self._registers_since_sweep >= _SWEEP_EVERY_REGISTERS
                and now - self._last_sweep_at >= _SWEEP_MIN_INTERVAL_SECONDS
            ):
                self._evict_expired_locked(now)
            pending_nodes: list[tuple[str, str | None, str, int]] = []
            pending_blobs: dict[str, bytes] = {}
            for node in sequence.nodes:
                content = sequence.blobs.get(node.blob_hash)
                if content is None:
                    return False
                held_node = self._nodes.get(node.seq_hash)
                if held_node is None:
                    pending_nodes.append((node.seq_hash, node.prev_hash, node.blob_hash, node.depth))
                else:
                    self._nodes[node.seq_hash] = (held_node[0], held_node[1], held_node[2], now)
                held_blob = self._blobs.get(node.blob_hash)
                if held_blob is None:
                    pending_blobs.setdefault(node.blob_hash, content)
                else:
                    self._blobs[node.blob_hash] = (held_blob[0], now)
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
            for seq_hash, prev_hash, blob_hash, depth in pending_nodes:
                self._nodes[seq_hash] = (prev_hash, blob_hash, depth, now)
                self._stored_bytes += _NODE_BYTES
            for blob_hash, content in pending_blobs.items():
                self._blobs[blob_hash] = (content, now)
                self._stored_bytes += len(content)
            return True

    def sequence(self, seq_hash: str) -> AddressedSequence | None:
        """Rebuild the addressed sequence a chain head names.

        The rebuild is a fresh view over shared entries: nodes and blobs are
        held once by the registry, and the returned
        :class:`AddressedSequence` is the only per-caller copy, discarded
        with the record that asked for it. Every entry the walk visits has
        its last-touched stamp refreshed, which is what keeps a live chain
        -- shared prefixes included -- from aging out.
        """
        with self._lock:
            now = _clock()
            nodes: list[SequenceNode] = []
            blobs: dict[str, bytes] = {}
            key: str | None = seq_hash
            while key:
                entry = self._nodes.get(key)
                if entry is None:
                    return None
                prev_hash, blob_hash, depth, _touched = entry
                content_entry = self._blobs.get(blob_hash)
                if content_entry is None:
                    return None
                self._nodes[key] = (prev_hash, blob_hash, depth, now)
                self._blobs[blob_hash] = (content_entry[0], now)
                nodes.append(SequenceNode(seq_hash=key, prev_hash=prev_hash, blob_hash=blob_hash, depth=depth))
                blobs[blob_hash] = content_entry[0]
                key = prev_hash
            if not nodes:
                return None
            nodes.reverse()
            return AddressedSequence(
                key="",
                seq_hash=nodes[-1].seq_hash,
                depth=len(nodes),
                logical_bytes=sum(len(content) for content in blobs.values()),
                nodes=tuple(nodes),
                blobs=blobs,
            )

    def _evict_expired_locked(self, now: float) -> None:
        """Reclaim entries nothing has touched for the TTL; caller holds the lock.

        Nodes and their elements are touched as pairs by registration and by
        every walk, so their stamps stay in step and both age out together;
        an entry that somehow survives alone makes its chain resolve as
        missing, which the readers already treat as a stated warning.
        """
        self._last_sweep_at = now
        self._registers_since_sweep = 0
        deadline = now - _TTL_SECONDS
        reclaimed = 0
        dead_nodes = [seq_hash for seq_hash, entry in self._nodes.items() if entry[3] < deadline]
        for seq_hash in dead_nodes:
            del self._nodes[seq_hash]
            self._stored_bytes -= _NODE_BYTES
            reclaimed += 1
        dead_blobs = [blob_hash for blob_hash, entry in self._blobs.items() if entry[1] < deadline]
        for blob_hash in dead_blobs:
            content, _touched = self._blobs.pop(blob_hash)
            self._stored_bytes -= len(content)
            reclaimed += 1
        if reclaimed:
            logger.info(
                "sequence registry reclaimed {} entries past the {}s TTL",
                reclaimed,
                _TTL_SECONDS,
            )
            # Reclaiming re-arms the one-shot cap warning, so a later cap hit
            # is not silent just because an earlier one already logged.
            self._warned = False

    def materialize(self, seq_hash: str) -> str | None:
        """Return the attribute value a chain head states, rebuilt verbatim.

        The caller attached the value with compact separators, so rebuilding
        from the per-element blobs reproduces the attached bytes exactly.
        """
        sequence = self.sequence(seq_hash)
        if sequence is None:
            return None
        elements = [sequence.blobs[node.blob_hash].decode("utf-8") for node in sequence.nodes]
        return rebuild_value(elements)

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
