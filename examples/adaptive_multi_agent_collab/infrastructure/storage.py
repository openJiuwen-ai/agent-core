"""Verified immutable evidence and small checkpoint storage envelopes.

These are storage contracts, not scientific/checkpoint-schema changes.  Legacy
checkpoint and JSON ledger files remain readable.  New immutable objects are
durable before any reference to them may be published.
"""

from __future__ import annotations

from ..paths import PACKAGE_ROOT
import hashlib
import json
import os
from pathlib import Path
import copy
import contextvars
import functools
import io
import inspect
import math
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Mapping

OBJECT_PROTOCOL = "immutable_json_v1"
LEDGER_PROTOCOL = "referenced_ledger_v1"
AMENDMENT_PROTOCOL = "checkpoint_amendment_v1"
COMPACT_PROTOCOL = "compact_checkpoint_v1"
COMPONENT_PROTOCOL = "component_checkpoint_v1"
TENSOR_PROTOCOL = "immutable_torch_component_v1"
STORAGE_DEADLINE = 5.0


class StorageFailure(RuntimeError):
    """Missing, corrupt, ambiguous or incompletely published durable evidence."""


_STARTUP: contextvars.ContextVar[StartupVerification | None] = contextvars.ContextVar(
    "adaptive_startup_verification", default=None
)


def current_startup() -> StartupVerification | None:
    """The bounded, process-local verification scope; never a persistent cache."""
    value = _STARTUP.get()
    return value if value is not None and not value._invalid else None


def startup_scope(function: Callable[..., Any]) -> Callable[..., Any]:
    """Share verification only within this startup call, including nested hooks."""
    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def asynchronous(*args: Any, **kwargs: Any) -> Any:
            if current_startup() is not None:
                return await function(*args, **kwargs)
            with StartupVerification():
                return await function(*args, **kwargs)
        return asynchronous

    @functools.wraps(function)
    def synchronous(*args: Any, **kwargs: Any) -> Any:
        if current_startup() is not None:
            return function(*args, **kwargs)
        with StartupVerification():
            return function(*args, **kwargs)
    return synchronous


class StartupVerification:
    """One pinned startup snapshot, invalidated before its first durable mutation.

    Immutable contents are checked against their hashes on first use. The final
    lock-protected freshness pass rehashes every dependency, including the whole
    journal. Cached values never constitute permission after that scope ends.
    """

    def __init__(self, *, max_cached_bytes: int = 8 * 1024 * 1024 * 1024,
                 max_objects: int = 32768) -> None:
        if max_cached_bytes < 1 or max_objects < 1:
            raise ValueError("startup cache bounds must be positive")
        self.max_cached_bytes = max_cached_bytes
        self.max_objects = max_objects
        self._files: dict[Path, tuple[str, int] | None] = {}
        self._cache: dict[Any, Any] = {}
        self._cache_bytes = 0
        self._retained_ids: set[int] = set()
        self._invalid = False
        self._lock = threading.RLock()
        self.metrics: dict[str, int | float] = {
            "file_reads": 0, "bytes_read": 0, "hash_bytes": 0,
            "cache_hits": 0, "objects_verified": 0, "checkpoint_decodes": 0,
            "journal_replays": 0, "freshness_passes": 0, "freshness_seconds": 0.0,
        }

    def __enter__(self) -> StartupVerification:
        self._token = _STARTUP.set(self)
        return self

    def __exit__(self, *_args: Any) -> None:
        _STARTUP.reset(self._token)
        self.invalidate()

    def invalidate(self) -> None:
        with self._lock:
            self._invalid = True
            self._cache.clear()
            self._retained_ids.clear()
            self._files.clear()
            self._cache_bytes = 0
            self.metrics["cache_retained_estimated_bytes"] = 0

    finish = invalidate

    def _healthy(self) -> None:
        if self._invalid:
            raise StorageFailure("startup verification snapshot was invalidated")

    def cached(self, key: Any) -> Any:
        with self._lock:
            self._healthy()
            if key in self._cache:
                self.metrics["cache_hits"] += 1
                return self._cache[key]
            return None

    def remember(self, key: Any, value: Any, *, size: int = 0) -> Any:
        with self._lock:
            self._healthy()
            if key in self._cache:
                return value
            if len(self._cache) >= self.max_objects:
                self.metrics["cache_budget_declines"] = self.metrics.get("cache_budget_declines", 0) + 1
                return value
            retained_bytes, retained_ids = self._retained_size(key, value)
            # Exceeding a cache budget only disables reuse for that value. It
            # does not weaken validation or impose a new scientific input cap.
            if (len(self._cache) < self.max_objects
                    and self._cache_bytes + retained_bytes <= self.max_cached_bytes):
                self._cache[key] = value
                self._cache_bytes += retained_bytes
                self._retained_ids.update(retained_ids)
                self.metrics["cache_retained_estimated_bytes"] = self._cache_bytes
                self.metrics["cache_retained_peak_bytes"] = max(
                    self._cache_bytes, self.metrics.get("cache_retained_peak_bytes", 0)
                )
                self.metrics["cache_source_bytes"] = self.metrics.get("cache_source_bytes", 0) + size
            else:
                self.metrics["cache_budget_declines"] = self.metrics.get("cache_budget_declines", 0) + 1
            return value

    def _retained_size(self, key: Any, value: Any) -> tuple[int, set[int]]:
        """Account unique retained Python nodes, not a huge re-encoded JSON copy."""
        pending = [key, value]
        seen: set[int] = set()
        total = 0
        while pending:
            item = pending.pop()
            identity = id(item)
            if identity in self._retained_ids or identity in seen:
                continue
            tracked = isinstance(item, (dict, list, tuple, set, frozenset, _VerifiedStamp))
            if tracked:
                seen.add(identity)
                # Account the retained identity integer and hash-set capacity.
                # Repeated scalar values are conservatively charged again;
                # tracking every string/number would itself amplify memory.
                total += 96
            total += sys.getsizeof(item)
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (list, tuple, set, frozenset)):
                pending.extend(item)
            if isinstance(item, (EvidenceDict, EvidenceList)):
                pending.extend((item._evidence_stamp, item._evidence_root,
                                item._evidence_parents,
                                getattr(item, "_verified_json_identity", None)))
            elif isinstance(item, _VerifiedStamp):
                pending.extend((item.reference, item.directory, item.dirty))
            # Tensor object headers exclude their storage. Counting each tensor
            # storage again is conservative for shared tensor views.
            if type(item).__module__.startswith("torch") and hasattr(item, "numel"):
                total += item.numel() * item.element_size()
        return total, seen

    def _pin(self, path: Path, digest: str, length: int) -> None:
        with self._lock:
            self._healthy()
            prior = self._files.get(path)
            if path in self._files and prior != (digest, length):
                self.invalidate()
                raise StorageFailure("startup dependency changed during verification")
            self._files[path] = (digest, length)
            self.metrics["file_reads"] += 1
            self.metrics["bytes_read"] += length
            self.metrics["hash_bytes"] += length

    def pin_absence(self, path: str | Path) -> None:
        path = Path(path).resolve()
        self._healthy()
        if path in self._files and self._files[path] is not None:
            self.invalidate()
            raise StorageFailure("startup dependency disappeared during verification")
        self._files[path] = None

    def read_bytes(self, path: str | Path) -> bytes:
        path = Path(path).resolve()
        self._healthy()
        raw = path.read_bytes()
        self._pin(path, hashlib.sha256(raw).hexdigest(), len(raw))
        return raw

    def file_sha256(self, path: str | Path) -> str:
        path = Path(path).resolve()
        self._healthy()
        if path in self._files:
            self.metrics["cache_hits"] += 1
            value = self._files[path]
            if value is None:
                raise StorageFailure("previously absent startup dependency appeared")
            return value[0]
        with self.verifying_stream(path) as stream:
            for _block in iter(lambda: stream.read(1024 * 1024), b""):
                pass
        value = self._files[path]
        assert value is not None
        return value[0]

    @contextmanager
    def verifying_stream(self, path: str | Path) -> Any:
        path = Path(path).resolve()
        self._healthy()
        digest, length = hashlib.sha256(), 0

        class Reader:
            def __init__(self, stream: Any) -> None:
                self.stream = stream

            def read(self, size: int = -1) -> bytes:
                nonlocal length
                raw = self.stream.read(size)
                digest.update(raw)
                length += len(raw)
                return raw

            def __iter__(self) -> Any:
                nonlocal length
                for raw in self.stream:
                    digest.update(raw)
                    length += len(raw)
                    yield raw

        with path.open("rb") as stream:
            yield Reader(stream)
        self._pin(path, digest.hexdigest(), length)

    def assert_fresh(self) -> None:
        """Call under campaign/writer locks immediately before execution setup."""
        started = time.perf_counter()
        with self._lock:
            self._healthy()
            try:
                for path, expected in self._files.items():
                    if expected is None:
                        if path.exists():
                            raise StorageFailure("previously absent startup dependency appeared")
                        continue
                    digest, length = hashlib.sha256(), 0
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                            length += len(block)
                    self.metrics["file_reads"] += 1
                    self.metrics["bytes_read"] += length
                    self.metrics["hash_bytes"] += length
                    if (digest.hexdigest(), length) != expected:
                        raise StorageFailure("startup dependency changed before execution")
            except BaseException:
                self.invalidate()
                raise
            self.metrics["freshness_passes"] += 1
            self.metrics["freshness_seconds"] += time.perf_counter() - started


class EvidenceReference(dict):
    """Internal reference, distinct from equally named fields in raw evidence."""


class CollectionReference(EvidenceReference):
    """Versioned incremental collection reference, never raw provider text."""


class TensorReference(EvidenceReference):
    """A verified immutable numerical snapshot, not a live tensor alias."""


def _escape(value: Any) -> Any:
    if isinstance(value, EvidenceReference):
        return value
    if isinstance(value, dict):
        if set(value) in ({"$evidence"}, {"$literal_evidence"}, {"$collection"}, {"$tensor"}):
            return {"$literal_evidence": [[key, _escape(item)] for key, item in value.items()]}
        return {key: _escape(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_escape(item) for item in value]
    return value


class _VerifiedStamp:
    __slots__ = ("reference", "directory", "dirty")

    def __init__(self, reference: dict[str, Any], directory: Path) -> None:
        self.reference = reference
        self.directory = directory
        self.dirty = False


def _unverified_stamp() -> _VerifiedStamp:
    stamp = _VerifiedStamp({}, PACKAGE_ROOT)
    stamp.dirty = True
    return stamp


class EvidenceDict(dict):
    """A verified JSON mapping whose descendants invalidate reusable provenance."""

    __slots__ = ("_evidence_stamp", "_evidence_root", "_evidence_parents",
                 "_verified_json_identity")

    def __init__(self, value: Any = (), stamp: _VerifiedStamp | None = None, root: bool = False) -> None:
        dict.__init__(self, value)
        self._evidence_stamp = stamp if stamp is not None else _unverified_stamp()
        self._evidence_root = root
        self._evidence_parents: tuple[_VerifiedStamp, ...] = ()

    def __deepcopy__(self, memo: dict[int, Any]) -> EvidenceDict:
        result = EvidenceDict({}, copy.deepcopy(self._evidence_stamp, memo), self._evidence_root)
        memo[id(self)] = result
        result._evidence_parents = copy.deepcopy(self._evidence_parents, memo)
        for key, value in self.items():
            dict.__setitem__(result, copy.deepcopy(key, memo), copy.deepcopy(value, memo))
        return result

    def __reduce_ex__(self, _protocol: int) -> Any:
        # Provenance exists only after hash-verified JSON loading. Never pickle
        # these stamps into checkpoint metadata or trust them after unpickling.
        return dict, (dict(self),)

    def __setitem__(self, key: Any, value: Any) -> None:
        _dirty(self)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: Any) -> None:
        _dirty(self)
        dict.__delitem__(self, key)

    def clear(self) -> None:
        _dirty(self)
        dict.clear(self)

    def update(self, *args: Any, **kwargs: Any) -> None:
        _dirty(self)
        dict.update(self, *args, **kwargs)

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key not in self:
            _dirty(self)
        return dict.setdefault(self, key, default)

    def pop(self, *args: Any) -> Any:
        _dirty(self)
        return dict.pop(self, *args)

    def popitem(self) -> Any:
        _dirty(self)
        return dict.popitem(self)

    def __ior__(self, other: Any) -> EvidenceDict:
        self.update(other)
        return self


class EvidenceList(list):
    """A verified JSON sequence sharing its enclosing evidence mutation stamp."""

    __slots__ = ("_evidence_stamp", "_evidence_root", "_evidence_parents",
                 "_verified_json_identity")

    def __init__(self, value: Any = (), stamp: _VerifiedStamp | None = None, root: bool = False) -> None:
        list.__init__(self, value)
        self._evidence_stamp = stamp if stamp is not None else _unverified_stamp()
        self._evidence_root = root
        self._evidence_parents: tuple[_VerifiedStamp, ...] = ()

    def __deepcopy__(self, memo: dict[int, Any]) -> EvidenceList:
        result = EvidenceList([], copy.deepcopy(self._evidence_stamp, memo), self._evidence_root)
        memo[id(self)] = result
        result._evidence_parents = copy.deepcopy(self._evidence_parents, memo)
        list.extend(result, (copy.deepcopy(item, memo) for item in self))
        return result

    def __reduce_ex__(self, _protocol: int) -> Any:
        return list, (list(self),)

    def __setitem__(self, key: Any, value: Any) -> None:
        _dirty(self)
        list.__setitem__(self, key, value)

    def __delitem__(self, key: Any) -> None:
        _dirty(self)
        list.__delitem__(self, key)

    def append(self, value: Any) -> None:
        _dirty(self)
        list.append(self, value)

    def extend(self, value: Any) -> None:
        _dirty(self)
        list.extend(self, value)

    def insert(self, index: int, value: Any) -> None:
        _dirty(self)
        list.insert(self, index, value)

    def clear(self) -> None:
        _dirty(self)
        list.clear(self)

    def pop(self, index: int = -1) -> Any:
        _dirty(self)
        return list.pop(self, index)

    def remove(self, value: Any) -> None:
        _dirty(self)
        list.remove(self, value)

    def reverse(self) -> None:
        _dirty(self)
        list.reverse(self)

    def sort(self, *args: Any, **kwargs: Any) -> None:
        _dirty(self)
        list.sort(self, *args, **kwargs)

    def __iadd__(self, value: Any) -> EvidenceList:
        self.extend(value)
        return self

    def __imul__(self, value: int) -> EvidenceList:
        _dirty(self)
        list.__imul__(self, value)
        return self


def _readonly(*_args: Any, **_kwargs: Any) -> Any:
    raise TypeError("verified startup evidence is read-only; materialize before mutation")


class FrozenEvidenceDict(EvidenceDict):
    """Shared immutable JSON; trainable tensors are never stored in these nodes."""

    __slots__ = ()

    __setitem__ = __delitem__ = clear = update = setdefault = pop = popitem = __ior__ = _readonly

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenEvidenceDict:
        return self


class FrozenEvidenceList(EvidenceList):
    """Shared immutable sequence preserving JSON list and tuple equivalence."""

    __slots__ = ()

    __setitem__ = __delitem__ = append = extend = insert = clear = pop = remove = _readonly
    reverse = sort = __iadd__ = __imul__ = _readonly

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenEvidenceList:
        return self


def freeze_json(value: Any, stamp: _VerifiedStamp | None = None, *, root: bool = False) -> Any:
    """Freeze JSON containers, retaining independently verified nested identities."""
    if isinstance(value, (FrozenEvidenceDict, FrozenEvidenceList)):
        return value
    # Unverified trees share one dirty stamp, just as verified trees share their
    # explicit stamp. Per-node stamps add empty dictionaries and Paths for every
    # nested audit object without providing any additional integrity guarantee.
    if stamp is None and isinstance(value, (dict, list, tuple)):
        stamp = _unverified_stamp()
    if isinstance(value, dict):
        result = FrozenEvidenceDict(
            {key: freeze_json(item, stamp) for key, item in value.items()}, stamp, root
        )
        return result
    if isinstance(value, (list, tuple)):
        return FrozenEvidenceList([freeze_json(item, stamp) for item in value], stamp, root)
    return value


def mutable_json(value: Any) -> Any:
    """Independent mutable JSON view; no caller can mutate a cached audit object."""
    if isinstance(value, dict):
        return {key: mutable_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [mutable_json(item) for item in value]
    return value


def verified_reference(value: Any) -> tuple[str, str, int | str] | None:
    inline = getattr(value, "_verified_json_identity", None)
    if inline is not None and isinstance(value, (FrozenEvidenceDict, FrozenEvidenceList)):
        return inline
    stamp = getattr(value, "_evidence_stamp", None)
    if (stamp is not None and getattr(value, "_evidence_root", False)
            and not stamp.dirty and set(stamp.reference) == {"$evidence"}):
        reference = stamp.reference["$evidence"]
        return str(stamp.directory), reference["sha256"], reference["bytes"]
    if (stamp is not None and getattr(value, "_evidence_root", False)
            and not stamp.dirty and set(stamp.reference) == {"$collection"}):
        reference = stamp.reference["$collection"]["node"]["$evidence"]
        return "collection:" + str(stamp.directory), reference["sha256"], reference["bytes"]
    return None


def validate_json(value: Any) -> None:
    """Strict JSON validation without repeatedly rendering verified raw evidence."""
    if verified_reference(value) is not None:
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            canonical_bytes(value)  # Preserve the existing canonical convention.
            return
        for item in value.values():
            validate_json(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_json(item)
    elif value is None or isinstance(value, (str, bool, int)):
        return
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite accounting value")
    else:
        canonical_bytes(value)  # Reject unsupported objects; never stringify them.


def strict_json_equal(left: Any, right: Any) -> bool:
    """Exact JSON comparison, with tuple/list equivalence and ordered sequences."""
    left_ref, right_ref = verified_reference(left), verified_reference(right)
    if left_ref is not None and right_ref is not None and left_ref == right_ref:
        return True
    context = current_startup()
    cache_key = None
    if context is not None and left_ref is not None and right_ref is not None:
        cache_key = ("json_equal", frozenset((left_ref, right_ref)))
        cached = context.cached(cache_key)
        if cached is not None:
            return cached
    result = _strict_json_equal_contents(left, right)
    if cache_key is not None:
        assert context is not None
        context.remember(cache_key, result, size=1)
    return result


def _strict_json_equal_contents(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is bool and type(right) is bool and left == right
    if type(left) is int and type(right) is int:
        return left == right
    if type(left) is float and type(right) is float:
        if not math.isfinite(left) or not math.isfinite(right):
            raise ValueError("non-finite accounting value")
        return left == right and (left != 0.0 or math.copysign(1, left) == math.copysign(1, right))
    if isinstance(left, dict) and isinstance(right, dict):
        if (all(isinstance(key, str) for key in left)
                and all(isinstance(key, str) for key in right)):
            return left.keys() == right.keys() and all(
                strict_json_equal(left[key], right[key]) for key in left
            )
    elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            strict_json_equal(a, b) for a, b in zip(left, right)
        )
    return canonical_bytes(left) == canonical_bytes(right)


def strict_json_fingerprint(value: Any) -> str:
    """Mutation token for a prepared restore, not a scientific/content hash."""
    digest = hashlib.sha256()

    def visit(item: Any) -> None:
        reference = verified_reference(item)
        if reference is not None:
            digest.update(b"reference:")
            digest.update(canonical_bytes(reference))
        elif isinstance(item, dict) and all(isinstance(key, str) for key in item):
            digest.update(b"{")
            for key in sorted(item):
                digest.update(canonical_bytes(key))
                digest.update(b":")
                visit(item[key])
                digest.update(b",")
            digest.update(b"}")
        elif isinstance(item, (tuple, list)):
            digest.update(b"[")
            for child in item:
                visit(child)
                digest.update(b",")
            digest.update(b"]")
        else:
            digest.update(canonical_bytes(item))

    visit(value)
    return digest.hexdigest()


def canonical_sha256(value: Any, *, ensure_ascii: bool = True) -> str:
    """Preserve canonical content hashes, reusing only a pinned value signature."""
    context = current_startup()
    key = None
    if context is not None:
        key = ("canonical_sha256", ensure_ascii, strict_json_fingerprint(value))
        cached = context.cached(key)
        if cached is not None:
            return cached
    raw = json.dumps(value, ensure_ascii=ensure_ascii, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    if context is not None:
        context.metrics["canonical_hash_bytes"] = context.metrics.get("canonical_hash_bytes", 0) + len(raw)
        context.remember(key, digest, size=len(digest))
    return digest


def _freeze_checkpoint_audit(value: dict[str, Any], path: Path, digest: str,
                             prefix: str = "") -> None:
    """Legacy audit JSON may be shared; trainable/configuration state may not.

    These in-process identities are bound to the verified checkpoint bytes and
    exact field position. They are never storage references or persisted stamps.
    """
    hooks = value.get("hook_state")
    if isinstance(hooks, dict) and not isinstance(hooks, FrozenEvidenceDict):
        def frozen(item: Any, selector: str) -> Any:
            if isinstance(item, (FrozenEvidenceDict, FrozenEvidenceList)):
                return item
            validate_json(item)
            result = freeze_json(item)
            if isinstance(result, (FrozenEvidenceDict, FrozenEvidenceList)):
                result._verified_json_identity = ("checkpoint:" + str(path), digest, prefix + selector)
            return result

        ledger = hooks.get("expenditure_ledger")
        if isinstance(ledger, dict) and not isinstance(ledger.get("entries"), FrozenEvidenceList):
            ledger["entries"] = [frozen(item, f"hook_state.expenditure_ledger.entries.{index}")
                                 for index, item in enumerate(ledger.get("entries", []))]
        for name in ("evaluation_records", "pilot_observations"):
            if name in hooks and not isinstance(hooks[name], FrozenEvidenceList):
                hooks[name] = [frozen(item, f"hook_state.{name}.{index}")
                               for index, item in enumerate(hooks[name])]
        for name in ("evaluation_cache", "evaluation_query_cache", "calibration_query_cache"):
            if name in hooks and not isinstance(hooks[name], FrozenEvidenceDict):
                hooks[name] = {key: frozen(item, f"hook_state.{name}.{key}")
                               for key, item in hooks[name].items()}
        for name in ("safety_record", "termination_record"):
            if hooks.get(name) is not None:
                hooks[name] = frozen(hooks[name], f"hook_state.{name}")
    best = value.get("best_system")
    if isinstance(best, dict):
        _freeze_checkpoint_audit(best, path, digest, prefix + "best_system.")


def _dirty(value: Any) -> None:
    value._evidence_stamp.dirty = True
    for stamp in value._evidence_parents:
        stamp.dirty = True


def _attach_parent(value: Any, stamp: _VerifiedStamp) -> Any:
    if isinstance(value, (EvidenceDict, EvidenceList)):
        if all(existing is not stamp for existing in value._evidence_parents):
            value._evidence_parents = (*value._evidence_parents, stamp)
        children = value.values() if isinstance(value, dict) else value
        for child in children:
            _attach_parent(child, stamp)
    return value


def _verified(value: Any, stamp: _VerifiedStamp, *, root: bool = False) -> Any:
    # Nested immutable references retain their own verified identity while a
    # later child mutation invalidates every enclosing object's reusable stamp.
    if isinstance(value, (EvidenceDict, EvidenceList)):
        return _attach_parent(value, stamp)
    if isinstance(value, dict):
        return EvidenceDict({key: _verified(item, stamp) for key, item in value.items()}, stamp, root)
    if isinstance(value, list):
        return EvidenceList([_verified(item, stamp) for item in value], stamp, root)
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _decode_json(raw: bytes | str) -> Any:
    def invalid_constant(_value: str) -> Any:
        raise ValueError("non-finite JSON value")

    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite JSON value")
        return result

    return json.loads(raw, parse_constant=invalid_constant, parse_float=finite_float)


def file_sha256(path: str | Path) -> str:
    context = current_startup()
    if context is not None:
        return context.file_sha256(path)
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: str | Path) -> Any:
    """Read a pinned JSON dependency; active startup results are read-only."""
    path = Path(path).resolve()
    context = current_startup()
    key = ("json", path)
    if context is not None:
        cached = context.cached(key)
        if cached is not None:
            return cached
        raw = context.read_bytes(path)
        return context.remember(key, freeze_json(_decode_json(raw)), size=len(raw))
    return _decode_json(path.read_text())


def read_bytes(path: str | Path) -> bytes:
    """Pin bytes used by lineage or configuration validation in this startup."""
    context = current_startup()
    return Path(path).read_bytes() if context is None else context.read_bytes(path)


def _directory_fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdir_durable(path: Path) -> None:
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        # Persist the new name in its already-created parent as well as the
        # eventual evidence file's name in the leaf directory.
        _directory_fsync(directory.parent)


def _contained(base: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise StorageFailure("immutable evidence reference escapes its artifact root")
    resolved = (base / candidate).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise StorageFailure("immutable evidence reference follows an external symlink")
    return resolved


class EvidenceStore:
    """Content-addressed JSON evidence with explicit immutable identity caching.

    An identity is an owning caller's immutable version, never a mutable object's
    address.  Reusing that identity with another object is rejected.  Mutating a
    registered object is prohibited by the owning caller's freeze contract.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).resolve()
        self.publication_guard: Callable[[], None] | None = None
        self._identities: dict[Any, tuple[Any, dict[str, Any]]] = {}
        self.metrics: dict[str, float | int] = {
            "serialized_bytes": 0, "written_bytes": 0, "objects_written": 0,
            "objects_reused": 0, "serialization_seconds": 0.0,
            "hash_seconds": 0.0, "write_seconds": 0.0, "fsync_seconds": 0.0,
            "tensor_objects_written": 0, "tensor_serialized_bytes": 0,
            "tensor_copied_bytes": 0, "tensor_capture_seconds": 0.0,
        }

    def put_tensor(self, value: Any, *, before_publish: Callable[[], None] | None = None) -> TensorReference:
        """Write an isolated numerical component once, before committing its ref.

        The owner supplies a detached snapshot at an actual mutation boundary.
        There is deliberately no tensor-identity cache here: reuse requires the
        owner's checked mutation version, never a pointer to mutable parameters.
        """
        import torch

        started = time.monotonic()

        def guard() -> None:
            if self.publication_guard is not None:
                self.publication_guard()
            if time.monotonic() - started > STORAGE_DEADLINE:
                raise StorageFailure("numerical component publication exceeded five-second deadline")
            if before_publish is not None:
                before_publish()

        guard()
        at = time.perf_counter()
        stream = io.BytesIO()
        torch.save(value, stream)
        raw = stream.getvalue()
        self.metrics["serialization_seconds"] += time.perf_counter() - at
        self.metrics["serialized_bytes"] += len(raw)
        self.metrics["tensor_serialized_bytes"] += len(raw)
        guard()
        at = time.perf_counter()
        digest = hashlib.sha256(raw).hexdigest()
        self.metrics["hash_seconds"] += time.perf_counter() - at
        target = _contained(self.directory, str(Path(".evidence") / digest[:2] / (digest + ".pt")))
        if target.exists():
            if target.stat().st_size != len(raw) or file_sha256(target) != digest:
                raise StorageFailure("existing numerical component is corrupt")
            self.metrics["objects_reused"] += 1
        else:
            _mkdir_durable(target.parent)
            pending = target.with_name(target.name + ".pending-" + uuid.uuid4().hex)
            try:
                with pending.open("xb") as output:
                    at = time.perf_counter()
                    output.write(raw)
                    output.flush()
                    self.metrics["write_seconds"] += time.perf_counter() - at
                    at = time.perf_counter()
                    os.fsync(output.fileno())
                    self.metrics["fsync_seconds"] += time.perf_counter() - at
                guard()
                if target.exists():
                    raise StorageFailure("numerical component publication collision")
                pending.replace(target)
                at = time.perf_counter()
                _directory_fsync(target.parent)
                self.metrics["fsync_seconds"] += time.perf_counter() - at
                guard()
                self.metrics["objects_written"] += 1
                self.metrics["tensor_objects_written"] += 1
                self.metrics["written_bytes"] += len(raw)
            finally:
                pending.unlink(missing_ok=True)
        guard()
        return TensorReference({"$tensor": {"protocol": TENSOR_PROTOCOL,
            "path": str(target.relative_to(self.directory)), "sha256": digest, "bytes": len(raw)}})

    def put(self, value: Any, *, identity: Any = None,
            before_publish: Callable[[], None] | None = None) -> dict[str, Any]:
        if identity is not None and identity in self._identities:
            previous, reference = self._identities[identity]
            if previous is not value:
                raise StorageFailure("immutable evidence identity reused for a different value")
            stamp = getattr(value, "_evidence_stamp", None)
            if stamp is None or not stamp.dirty:
                self.metrics["objects_reused"] += 1
                return reference
            # A restored mutable receipt can acquire newer evidence.  Its
            # provenance must never make that mutation disappear on publication.
            self._identities.pop(identity)
        stamp = getattr(value, "_evidence_stamp", None)
        if (stamp is not None and getattr(value, "_evidence_root", False)
                and not stamp.dirty and stamp.directory == self.directory):
            reference = stamp.reference
            if identity is not None:
                self._identities[identity] = (value, reference)
            self.metrics["objects_reused"] += 1
            return reference
        started = time.monotonic()

        def guard() -> None:
            if self.publication_guard is not None:
                self.publication_guard()
            if time.monotonic() - started > STORAGE_DEADLINE:
                raise StorageFailure("immutable evidence publication exceeded five-second deadline")
            if before_publish is not None:
                before_publish()

        guard()
        at = time.perf_counter()
        raw = canonical_bytes(_escape(value))
        self.metrics["serialization_seconds"] += time.perf_counter() - at
        self.metrics["serialized_bytes"] += len(raw)
        at = time.perf_counter()
        digest = hashlib.sha256(raw).hexdigest()
        self.metrics["hash_seconds"] += time.perf_counter() - at
        relative = Path(".evidence") / digest[:2] / (digest + ".json")
        target = _contained(self.directory, str(relative))
        guard()
        if target.exists():
            if target.stat().st_size != len(raw) or file_sha256(target) != digest:
                raise StorageFailure("existing immutable evidence is corrupt")
            self.metrics["objects_reused"] += 1
        else:
            at = time.perf_counter()
            _mkdir_durable(target.parent)
            self.metrics["fsync_seconds"] += time.perf_counter() - at
            # A killed migration may leave an uncommitted scratch file.  A new
            # bounded attempt neither treats it as evidence nor overwrites it.
            pending = target.with_name(target.name + ".pending-" + uuid.uuid4().hex)
            try:
                with pending.open("xb") as stream:
                    at = time.perf_counter()
                    stream.write(raw)
                    stream.flush()
                    self.metrics["write_seconds"] += time.perf_counter() - at
                    at = time.perf_counter()
                    os.fsync(stream.fileno())
                    self.metrics["fsync_seconds"] += time.perf_counter() - at
                guard()
                # Writers are serialized by the campaign journal lock.  Never
                # overwrite an independently appeared immutable object.
                if target.exists():
                    raise StorageFailure("immutable evidence publication collision")
                pending.replace(target)
                at = time.perf_counter()
                _directory_fsync(target.parent)
                self.metrics["fsync_seconds"] += time.perf_counter() - at
                guard()
                self.metrics["written_bytes"] += len(raw)
                self.metrics["objects_written"] += 1
            finally:
                pending.unlink(missing_ok=True)
        reference = EvidenceReference({"$evidence": {"protocol": OBJECT_PROTOCOL,
                                  "path": str(relative), "sha256": digest,
                                  "bytes": len(raw)}})
        guard()
        if identity is not None:
            self._identities[identity] = (value, reference)
        return reference

    def compact_ledger(self, state: Mapping[str, Any], *,
                       before_publish: Callable[[], None] | None = None) -> dict[str, Any]:
        """Explicit migration/consolidation; ordinary mutations remain WAL deltas."""
        return {**{key: value for key, value in state.items() if key != "entries"},
                "entries": [self.put(entry, before_publish=before_publish)
                            for entry in state["entries"]]}

    def resolve(self, value: Any) -> Any:
        return resolve_references(value, self.directory)


def resolve_references(value: Any, directory: str | Path, *,
                       _active: frozenset[str] = frozenset()) -> Any:
    # Resolve the base once, not once per scalar in a large receipt history.
    return _resolve(value, Path(directory).resolve(), _active, {})


def compact_references(value: Any, directory: str | Path) -> Any:
    """Reuse verified immutable nodes after restore, without serializing history."""
    base = Path(directory).resolve()
    memo: dict[int, Any] = {}

    def visit(item: Any) -> Any:
        stamp = getattr(item, "_evidence_stamp", None)
        if (stamp is not None and getattr(item, "_evidence_root", False)
                and not stamp.dirty and stamp.directory == base):
            return stamp.reference
        if isinstance(item, (Mapping, list, tuple)) and id(item) in memo:
            return memo[id(item)]
        if isinstance(item, Mapping):
            result: Any = {}
            memo[id(item)] = result
            result.update((key, visit(nested)) for key, nested in item.items())
            return result
        if isinstance(item, list):
            result = []
            memo[id(item)] = result
            result.extend(visit(nested) for nested in item)
            return result
        if isinstance(item, tuple):
            result = tuple(visit(nested) for nested in item)
            memo[id(item)] = result
            return result
        return item

    return visit(value)


def _resolve(value: Any, base: Path, active: frozenset[str], memo: dict[int, Any]) -> Any:
    if isinstance(value, (Mapping, list, tuple)) and id(value) in memo:
        return memo[id(value)]
    if isinstance(value, Mapping):
        if set(value) == {"$collection"}:
            from .incremental import resolve_collection
            result = resolve_collection(value["$collection"],
                lambda node: _resolve(node, base, active, memo))
            stamp = _VerifiedStamp(CollectionReference(value), base)
            result = (freeze_json(result, stamp, root=True) if current_startup() is not None
                      else _verified(result, stamp, root=True))
            memo[id(value)] = result
            return result
        if set(value) == {"$tensor"}:
            import torch

            reference = value["$tensor"]
            if (not isinstance(reference, Mapping)
                    or set(reference) != {"protocol", "path", "sha256", "bytes"}
                    or reference["protocol"] != TENSOR_PROTOCOL):
                raise StorageFailure("invalid numerical component reference")
            path = _contained(base, reference["path"])
            context = current_startup()
            key = ("tensor-component", path, reference["sha256"], reference["bytes"])
            cached = context.cached(key) if context is not None else None
            if cached is not None:
                result = copy.deepcopy(cached)
            else:
                try:
                    raw = path.read_bytes() if context is None else context.read_bytes(path)
                except OSError as error:
                    raise StorageFailure("missing numerical component") from error
                digest = hashlib.sha256(raw).hexdigest() if context is None else context.file_sha256(path)
                if len(raw) != reference["bytes"] or digest != reference["sha256"]:
                    raise StorageFailure("modified numerical component")
                result = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=False)
                if context is not None:
                    context.remember(key, copy.deepcopy(result), size=len(raw))
            # Every read owns its tensors. Startup sharing must never alias a
            # restored optimizer/model with another caller's checkpoint view.
            memo[id(value)] = result
            return result
        if set(value) == {"$literal_evidence"}:
            result = {key: _resolve(item, base, active, memo)
                      for key, item in value["$literal_evidence"]}
            memo[id(value)] = result
            return result
        if set(value) == {"$evidence"}:
            reference = value["$evidence"]
            if (not isinstance(reference, Mapping)
                    or set(reference) != {"protocol", "path", "sha256", "bytes"}
                    or reference["protocol"] != OBJECT_PROTOCOL):
                raise StorageFailure("invalid immutable evidence reference")
            path = _contained(base, reference["path"])
            digest = reference["sha256"]
            if digest in active:
                raise StorageFailure("cyclic immutable evidence reference")
            context = current_startup()
            key = ("evidence", path, digest, reference["bytes"])
            if context is not None:
                cached = context.cached(key)
                if cached is not None:
                    memo[id(value)] = cached
                    return cached
            try:
                raw = path.read_bytes() if context is None else context.read_bytes(path)
            except OSError as error:
                raise StorageFailure("missing immutable evidence") from error
            actual_digest = hashlib.sha256(raw).hexdigest() if context is None else context.file_sha256(path)
            if len(raw) != reference["bytes"] or actual_digest != digest:
                raise StorageFailure("modified immutable evidence")
            try:
                decoded = _decode_json(raw)
            except (ValueError, TypeError) as error:
                raise StorageFailure("invalid immutable evidence JSON") from error
            resolved = _resolve(decoded, base, active | {digest}, {})
            stamp = _VerifiedStamp(EvidenceReference(value), base)
            if context is None:
                result = _verified(resolved, stamp, root=True)
            else:
                result = freeze_json(resolved, stamp, root=True)
                context.metrics["objects_verified"] += 1
                context.remember(key, result, size=len(raw))
            memo[id(value)] = result
            return result
        result = {}
        memo[id(value)] = result
        result.update((key, _resolve(item, base, active, memo)) for key, item in value.items())
        return result
    if isinstance(value, list):
        result = []
        memo[id(value)] = result
        result.extend(_resolve(item, base, active, memo) for item in value)
        return result
    if isinstance(value, tuple):
        result = tuple(_resolve(item, base, active, memo) for item in value)
        memo[id(value)] = result
        return result
    return value


def _evidence_root(value: Any, fallback: Path) -> Path:
    if value is None:
        return fallback.resolve()
    root = Path(value).resolve()
    if not root.is_relative_to(PACKAGE_ROOT):
        raise StorageFailure("evidence root is outside permitted artifacts")
    return root


def ledger_envelope(state: Mapping[str, Any], *, evidence_root: str | Path | None = None) -> dict[str, Any]:
    return {"storage_protocol": LEDGER_PROTOCOL, "state": state,
            **({"evidence_root": str(Path(evidence_root).resolve())} if evidence_root is not None else {})}


def load_ledger(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    value = read_json(path)
    if isinstance(value, dict) and value.get("storage_protocol") == LEDGER_PROTOCOL:
        if set(value) - {"storage_protocol", "state", "evidence_root"}:
            raise StorageFailure("invalid ledger storage envelope")
        return resolve_references(value["state"], _evidence_root(value.get("evidence_root"), path.parent))
    return copy.deepcopy(value)


def amendment_payload(parent_path: str | Path, parent_sha256: str,
                      patches: list[dict[str, Any]], *, target: str | Path) -> dict[str, Any]:
    """Create a small parent-bound envelope; the audited caller validates changes."""
    parent, target = Path(parent_path).resolve(), Path(target).resolve()
    if parent == target:
        raise StorageFailure("checkpoint amendment cannot reference itself")
    # A parent may be a preserved source under the same target package.  Explicit
    # absolute parent paths stay truthful when historical snapshots are moved.
    return {"storage_protocol": AMENDMENT_PROTOCOL,
            "parent": {"path": str(parent), "sha256": parent_sha256},
            "patches": patches}


def load_checkpoint(path: str | Path, *, _active: frozenset[Path] = frozenset()) -> dict[str, Any]:
    import torch

    path = Path(path).resolve()
    if path in _active:
        raise StorageFailure("cyclic checkpoint amendment")
    context = current_startup()
    cache_key = ("checkpoint", path)
    if context is not None:
        cached = context.cached(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)
        raw = context.read_bytes(path)
        value = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=False)
        context.metrics["checkpoint_decodes"] += 1
    else:
        value = torch.load(path, map_location="cpu", weights_only=False)
    if isinstance(value, dict) and value.get("storage_protocol") == AMENDMENT_PROTOCOL:
        if set(value) != {"storage_protocol", "parent", "patches"}:
            raise StorageFailure("invalid checkpoint amendment envelope")
        parent = Path(value["parent"]["path"]).resolve()
        package = PACKAGE_ROOT
        if not parent.is_relative_to(package) or parent in _active or parent == path:
            raise StorageFailure("checkpoint parent outside permitted artifacts or cyclic")
        if file_sha256(parent) != value["parent"]["sha256"]:
            raise StorageFailure("checkpoint amendment parent changed")
        value_parent = load_checkpoint(parent, _active=_active | {path})
        seen: set[tuple[str, ...]] = set()
        for patch in value["patches"]:
            if set(patch) != {"path", "value"} or not patch["path"] or not all(
                    isinstance(key, str) for key in patch["path"]):
                raise StorageFailure("invalid checkpoint amendment patch")
            keys = tuple(patch["path"])
            if any(keys[:len(old)] == old or old[:len(keys)] == keys for old in seen):
                raise StorageFailure("overlapping checkpoint amendment patches")
            seen.add(keys)
            node = value_parent
            for key in keys[:-1]:
                if key not in node or not isinstance(node[key], dict):
                    raise StorageFailure("checkpoint amendment patch has no parent")
                if isinstance(node[key], FrozenEvidenceDict):
                    node[key] = dict(node[key])
                node = node[key]
            node[keys[-1]] = resolve_references(patch["value"], path.parent)
        if context is not None:
            context.remember(cache_key, copy.deepcopy(value_parent), size=len(raw))
        return value_parent
    if not isinstance(value, dict):
        raise StorageFailure("checkpoint payload is not a mapping")
    if value.get("storage_protocol") in {COMPACT_PROTOCOL, COMPONENT_PROTOCOL}:
        value = dict(value)
        value.pop("storage_protocol")
        root = _evidence_root(value.pop("storage_root", None), path.parent)
        value = resolve_references(value, root)
    if context is not None:
        _freeze_checkpoint_audit(value, path, context.file_sha256(path))
        context.remember(cache_key, copy.deepcopy(value), size=len(raw))
    return value
