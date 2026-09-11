# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Content-addressed sequences for the trajectory storage path.

The GenAI convention has every model call state its whole input: the messages,
the tool definitions, the system instructions. That is right for the protocol
and wasteful for storage -- consecutive calls of one conversation repeat almost
all of it. Measured on one real session, 803 calls carried three distinct tool
definitions, and 57,550 message slots held 1,991 distinct messages.

So the wire keeps stating everything and storage stops repeating it. Each such
attribute is treated as a *sequence*, every element is addressed by the hash of
its content, and the sequence itself is a hash chain:

    seq(0)  = H(""    || H(element 0))
    seq(i)  = H(seq(i-1) || H(element i))

Two properties follow, and both are load-bearing for the reader:

* Equal ``seq_hash`` means every element is equal, in order. A reader holding
  the hash knows nothing changed without fetching anything.
* A shared prefix produces shared chain nodes, so the fork between two versions
  is where their chains stop agreeing -- which is what a diff is.

Nothing here holds state. The chain of a sequence depends only on its content,
so the same messages always build the same nodes, whatever order spans are
encoded in and whichever thread encodes them. Deduplication is the storage
layer inserting a node it already has.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

# Version prefix of a stored reference. It exists so a later change of hash
# algorithm or node shape stays recognisable next to what is already stored.
SEQUENCE_REFERENCE_VERSION = "1"
SEQUENCE_REFERENCE_PREFIX = "@oj-seq"

# Element JSON is written compactly and read back the same way. Byte-identical
# round-tripping of the original attribute is deliberately not a goal: the
# original spacing carries no meaning, and reproducing it would make the hash
# depend on how a caller happened to serialize its messages.
_COMPACT_SEPARATORS = (",", ":")


@dataclass(frozen=True, slots=True)
class SequenceNode:
    """One prefix of a sequence, addressed by the content of that prefix."""

    seq_hash: str
    prev_hash: str | None
    blob_hash: str
    depth: int


@dataclass(frozen=True, slots=True)
class AddressedSequence:
    """One attribute value expressed as a chain, plus what it introduced.

    Attributes:
        key: Attribute the sequence was built from.
        seq_hash: Hash of the complete sequence; the reference a record keeps.
        depth: Number of elements.
        logical_bytes: Length of the attribute value before it was replaced.
            The read path budgets pages by this, because a reader receives the
            rebuilt value rather than the reference.
        nodes: Chain nodes of the whole sequence, shallowest first.
        blobs: Element content by hash.
    """

    key: str
    seq_hash: str
    depth: int
    logical_bytes: int
    nodes: tuple[SequenceNode, ...]
    blobs: dict[str, bytes]


def content_hash(content: bytes) -> str:
    """Return the address of one piece of content."""
    return hashlib.sha256(content).hexdigest()


def _chain_hash(previous: str | None, blob_hash: str) -> str:
    digest = hashlib.sha256()
    digest.update((previous or "").encode("ascii"))
    digest.update(b"\x00")
    digest.update(blob_hash.encode("ascii"))
    return digest.hexdigest()


def sequence_reference(seq_hash: str, depth: int) -> str:
    """Return the value stored in place of a sequence."""
    return f"{SEQUENCE_REFERENCE_PREFIX}:{SEQUENCE_REFERENCE_VERSION}:{seq_hash}:{depth}"


def parse_sequence_reference(value: object) -> tuple[str, int] | None:
    """Return the hash and depth a reference names, or None for a plain value.

    Args:
        value: Attribute value to inspect.

    Returns:
        The sequence hash and its depth, or None when *value* is not a
        reference this version understands.
    """
    if not isinstance(value, str) or not value.startswith(SEQUENCE_REFERENCE_PREFIX):
        return None
    parts = value.split(":")
    if len(parts) != 4 or parts[1] != SEQUENCE_REFERENCE_VERSION:
        return None
    try:
        depth = int(parts[3])
    except ValueError:
        return None
    if not parts[2] or depth < 0:
        return None
    return parts[2], depth


def split_elements(value: str) -> list[str]:
    """Split one attribute value into the elements it states.

    Only an array has elements. Anything else -- an object, a bare string, a
    value that does not parse -- states nothing to address here, and saying
    so is what keeps a chain unambiguous: every chain is built from an array,
    so every chain rebuilds into one, whatever its depth.

    Treating a scalar as a sequence of one made it indistinguishable from a
    single-element array, and left both rebuild paths guessing which they
    held. A one-message answer -- the ordinary case for an assistant turn --
    is exactly where that guess went wrong.

    Args:
        value: The attribute value as the convention states it.

    Returns:
        Each element serialized compactly, in order, or nothing at all when
        the value is not an array.
    """
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [
        json.dumps(element, ensure_ascii=False, separators=_COMPACT_SEPARATORS)
        for element in parsed
    ]


def build_sequence(key: str, value: str) -> AddressedSequence | None:
    """Express one attribute value as a chain of content-addressed elements.

    Args:
        key: Attribute the value belongs to.
        value: The value as stated on the span.

    Returns:
        The addressed sequence, or None when the value states nothing to
        address -- an empty value, an empty array, or anything that is not
        an array at all. A caller that gets None leaves the value as it is.
    """
    if not value:
        return None
    elements = split_elements(value)
    if not elements:
        return None
    nodes: list[SequenceNode] = []
    blobs: dict[str, bytes] = {}
    previous: str | None = None
    for depth, element in enumerate(elements, start=1):
        encoded = element.encode("utf-8")
        blob_hash = content_hash(encoded)
        blobs[blob_hash] = encoded
        seq_hash = _chain_hash(previous, blob_hash)
        nodes.append(SequenceNode(
            seq_hash=seq_hash,
            prev_hash=previous,
            blob_hash=blob_hash,
            depth=depth,
        ))
        previous = seq_hash
    return AddressedSequence(
        key=key,
        seq_hash=nodes[-1].seq_hash,
        depth=len(nodes),
        logical_bytes=len(value.encode("utf-8")),
        nodes=tuple(nodes),
        blobs=blobs,
    )


def rebuild_value(elements: list[str]) -> str:
    """Return the attribute value a sequence of elements states.

    Always an array: a chain is built from one, so it rebuilds into one at
    any depth. Nothing here inspects the count.
    """
    return "[" + ",".join(elements) + "]"


def addressable_attributes(payload: Any, keys: frozenset[str]) -> list[dict[str, Any]]:
    """Return the attribute entries of an OTLP payload that *keys* names.

    Args:
        payload: One decoded OTLP ``ExportTraceServiceRequest``.
        keys: Attribute keys to address.

    Returns:
        The matching attribute entries, in document order, so a caller can
        replace their values in place.
    """
    found: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return found
    for resource_span in payload.get("resourceSpans") or ():
        if not isinstance(resource_span, dict):
            continue
        for scope_span in resource_span.get("scopeSpans") or ():
            if not isinstance(scope_span, dict):
                continue
            for span in scope_span.get("spans") or ():
                if not isinstance(span, dict):
                    continue
                for attribute in span.get("attributes") or ():
                    if not isinstance(attribute, dict):
                        continue
                    if attribute.get("key") in keys:
                        found.append(attribute)
    return found


__all__ = [
    "AddressedSequence",
    "SEQUENCE_REFERENCE_PREFIX",
    "SEQUENCE_REFERENCE_VERSION",
    "SequenceNode",
    "addressable_attributes",
    "build_sequence",
    "content_hash",
    "parse_sequence_reference",
    "rebuild_value",
    "sequence_reference",
    "split_elements",
]
