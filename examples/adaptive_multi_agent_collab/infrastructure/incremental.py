"""Mutation-tracked JSON collections for bounded checkpoint evidence chains.

Only changed members are rendered. Every 32 publications the reference index is
consolidated at the caller's checkpoint boundary; historical payloads stay in
the existing immutable evidence store. These are storage contracts, not model
or scientific history transformations.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Callable, Mapping, Self, SupportsIndex, cast

COLLECTION_PROTOCOL = "incremental_collection_v1"
MAX_CHAIN_DEPTH = 32


def _wrap(value: Any, changed: Callable[[], None]) -> Any:
    from .storage import EvidenceReference

    if isinstance(value, EvidenceReference):
        return value
    if isinstance(value, dict):
        result = TrackedDict(value, _changed=changed)
        retain_verified(result, value)
        return result
    if isinstance(value, (list, tuple)):
        # Tracked owners contain JSON metadata. Establish its ordered-array
        # representation at ownership, so a live dataclass projection and its
        # JSON round trip agree. Numerical/RNG tuples are never routed here;
        # mapping keys and non-container typed values remain untouched.
        sequence = TrackedList(value, _changed=changed)
        retain_verified(sequence, value)
        return sequence
    return value


def track(value: Any) -> Any:
    """Own mutable JSON containers once; leave already tracked owners intact."""
    if isinstance(value, (TrackedDict, TrackedList)):
        return value
    return _wrap(value, lambda: None)


def retain_verified(target: Any, source: Any) -> None:
    """Preserve validated storage provenance after an equivalent typed restore."""
    from .storage import _VerifiedStamp

    stamp = getattr(source, "_evidence_stamp", None)
    if (stamp is not None and not stamp.dirty and getattr(source, "_evidence_root", False)):
        target._evidence_stamp = _VerifiedStamp(stamp.reference, stamp.directory)
        target._evidence_root = True


class _Tracked:
    def _initialize(self, changed: Callable[[], None] | None) -> None:
        self._changed = changed
        self._revision = 0
        self._dirty: dict[Any, int] = {}
        self._structural_revision = 0
        self._snapshots: dict[Any, dict[str, Any]] = {}

    def _touch(self, key: Any) -> None:
        self._revision += 1
        self._dirty[key] = self._revision
        stamp = getattr(self, "_evidence_stamp", None)
        if stamp is not None:
            stamp.dirty = True
        if self._changed is not None:
            self._changed()

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        # Ordinary/legacy state readers receive independent plain containers,
        # never listeners, cached references or a mutable alias to live state.
        import copy
        result: Any = {} if isinstance(self, dict) else []
        memo[id(self)] = result
        if isinstance(self, dict):
            result.update((copy.deepcopy(k, memo), copy.deepcopy(v, memo)) for k, v in self.items())
        else:
            result.extend(copy.deepcopy(v, memo) for v in cast(Iterable[Any], self))
        return result


class TrackedDict(_Tracked, dict):
    def __init__(self, value: Mapping[Any, Any] | Iterable[tuple[Any, Any]] = (), *,
                 _changed: Callable[[], None] | None = None) -> None:
        dict.__init__(self)
        self._initialize(_changed)
        self.update(value)

    def __setitem__(self, key: Any, value: Any) -> None:
        dict.__setitem__(self, key, _wrap(value, lambda: self._touch(key)))
        self._touch(key)

    def __delitem__(self, key: Any) -> None:
        dict.__delitem__(self, key)
        self._touch(key)

    def update(self, *args: Any, **kwargs: Any) -> None:
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key: Any, *default: Any) -> Any:
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = self[key]
        del self[key]
        return value

    def popitem(self) -> tuple[Any, Any]:
        key = next(reversed(self))
        return key, self.pop(key)

    def clear(self) -> None:
        for key in tuple(self):
            del self[key]

    # Match dict's in-place contract (also accepts iterable key/value pairs),
    # which is deliberately broader than its inherited binary __or__ signature.
    def __ior__(self, other: Any) -> Self:  # type: ignore[misc]
        self.update(other)
        return self


class TrackedList(_Tracked, list):
    def __init__(self, value: Any = (), *, _changed: Callable[[], None] | None = None) -> None:
        list.__init__(self)
        self._initialize(_changed)
        self.extend(value)

    def append(self, value: Any) -> None:
        index = len(self)
        list.append(self, _wrap(value, lambda: self._touch(index)))
        self._touch(index)

    def extend(self, values: Any) -> None:
        for value in values:
            self.append(value)

    def _replace(self, values: Any) -> None:
        list.clear(self)
        self.extend(values)
        self._touch(-1)
        self._structural_revision = self._revision

    def __setitem__(self, index: Any, value: Any) -> None:
        if isinstance(index, slice):
            values = list(self)
            values[index] = value
            self._replace(values)
        else:
            if index < 0:
                index += len(self)
            list.__setitem__(self, index, _wrap(value, lambda: self._touch(index)))
            self._touch(index)

    def __delitem__(self, index: Any) -> None:
        values = list(self)
        del values[index]
        self._replace(values)

    def insert(self, index: SupportsIndex, value: Any) -> None:
        values = list(self)
        values.insert(index, value)
        self._replace(values)

    def pop(self, index: SupportsIndex = -1) -> Any:
        value = self[index]
        del self[index]
        return value

    def remove(self, value: Any) -> None:
        del self[self.index(value)]

    def clear(self) -> None:
        self._replace([])

    def reverse(self) -> None:
        self._replace(list(reversed(self)))

    def sort(self, *, key: Any = None, reverse: bool = False) -> None:
        self._replace(sorted(self, key=key, reverse=reverse))

    # Like list, += accepts any iterable while binary + requires a list.
    def __iadd__(self, other: Iterable[Any]) -> Self:  # type: ignore[misc]
        self.extend(other)
        return self

    def __imul__(self, count: SupportsIndex) -> Self:
        self._replace(list(self) * count)
        return self


def snapshot_collection(store: Any, source: Any, render: Callable[[Any], Any] | None = None) -> Any:
    """Commit changed members followed by a small, immutable collection node.

    Caller owns the collection until this synchronous writer operation returns.
    A concurrent mutation is rejected before publication; dirty revisions are
    retained until all referenced members and the node have become durable.
    """
    from .storage import CollectionReference, EvidenceReference, StorageFailure

    if not isinstance(source, (TrackedList, TrackedDict)):
        raise TypeError("incremental publication requires a tracked collection")
    stamp = getattr(source, "_evidence_stamp", None)
    if (stamp is not None and not stamp.dirty and stamp.directory == store.directory
            and set(stamp.reference) == {"$collection"}):
        return stamp.reference
    # These owning callers use fixed projections. Bound methods and boundary-
    # local closures may be recreated each save, but their semantics must not
    # depend on mutable closure state other than this store. A different
    # projection requires a different renderer; code identity is not a general
    # cache key for arbitrary user-provided transformation functions.
    renderer = getattr(render, "__func__", render)
    key = (store.directory, getattr(renderer, "__code__", renderer))
    previous = source._snapshots.get(key)
    revision = source._revision
    if previous is not None and previous["revision"] == revision:
        return previous["reference"]
    kind = "dict" if isinstance(source, dict) else "list"
    old_revision = -1 if previous is None else previous["revision"]
    reset = previous is None or source._structural_revision > old_revision
    consolidate = reset or previous["depth"] >= MAX_CHAIN_DEPTH
    refs = {} if reset else previous["members"]
    changed = list(source.keys()) if reset and isinstance(source, TrackedDict) else (
        list(range(len(source))) if reset else [k for k, stamp in source._dirty.items() if stamp > old_revision])
    updates = {}
    removed = []
    for member in changed:
        if (kind == "dict" and member not in source) or (kind == "list" and not 0 <= member < len(source)):
            if member in refs:
                removed.append(member)
            continue
        value = source[member]
        rendered = value if render is None else render(value)
        updates[member] = rendered if isinstance(rendered, EvidenceReference) else store.put(rendered)
    if source._revision != revision:
        raise StorageFailure("collection mutated during durable snapshot")
    # Do not mutate the last committed reference index until the collection
    # node is durable too. Otherwise a failed node write can erase a deletion
    # from the next attempt's delta while retaining its old parent reference.
    # Only explicit consolidation copies the historical reference index.
    members = updates
    if consolidate:
        members = {key: value for key, value in refs.items() if key not in removed}
        members.update(updates)
    node = {"protocol": COLLECTION_PROTOCOL, "kind": kind, "length": len(source),
            "parent": None if consolidate else previous["reference"],
            "members": [[k, v] for k, v in members.items()],
            "removed": [] if consolidate else removed}
    reference = CollectionReference({"$collection": {"protocol": COLLECTION_PROTOCOL,
        "kind": kind, "length": len(source), "node": store.put(node)}})
    if source._revision != revision:
        raise StorageFailure("collection mutated before snapshot completion")
    for member in removed:
        refs.pop(member)
    refs.update(updates)
    source._snapshots[key] = {"revision": revision, "reference": reference,
        "members": refs, "depth": 1 if consolidate else previous["depth"] + 1}
    return reference


def resolve_collection(reference: Mapping[str, Any], resolve: Callable[[Any], Any]) -> Any:
    """Validate and reconstruct one committed collection through verified nodes."""
    from .storage import StorageFailure

    if set(reference) != {"protocol", "kind", "length", "node"} or reference["protocol"] != COLLECTION_PROTOCOL:
        raise StorageFailure("invalid collection reference")
    node = resolve(reference["node"])
    if not isinstance(node, dict) or set(node) != {"protocol", "kind", "length", "parent", "members", "removed"}:
        raise StorageFailure("invalid collection node")
    if any(node[k] != reference[k] for k in ("protocol", "kind", "length")):
        raise StorageFailure("collection descriptor mismatch")
    kind = node["kind"]
    parent = node["parent"]
    if kind not in {"dict", "list"} or (parent is not None and not isinstance(parent, dict if kind == "dict" else list)):
        raise StorageFailure("invalid collection parent")
    result = dict(parent or {}) if kind == "dict" else dict(enumerate(parent or []))
    if not isinstance(node["members"], list) or not isinstance(node["removed"], list):
        raise StorageFailure("invalid collection changes")
    for key in node["removed"]:
        if key not in result:
            raise StorageFailure("collection removal did not exist")
        del result[key]
    seen = set()
    for pair in node["members"]:
        if not isinstance(pair, list) or len(pair) != 2:
            raise StorageFailure("invalid collection member")
        key, value = pair
        if key in seen:
            raise StorageFailure("duplicate collection update")
        seen.add(key)
        result[key] = value
    if len(result) != reference["length"]:
        raise StorageFailure("collection length mismatch")
    if kind == "dict":
        return result
    if set(result) != set(range(len(result))):
        raise StorageFailure("noncontiguous collection sequence")
    return [result[index] for index in range(len(result))]
