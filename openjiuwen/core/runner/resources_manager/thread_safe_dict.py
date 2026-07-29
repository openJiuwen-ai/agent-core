# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import (
    Dict, Generic, Iterator, KeysView, MutableMapping,
    Optional, TypeVar, ValuesView, ItemsView, Iterable, Mapping, Callable
)

K = TypeVar("K")
V = TypeVar("V")


class ThreadSafeDict(MutableMapping[K, V], Generic[K, V]):
    """In-memory dict wrapper used in asyncio single-threaded context.

    Named ThreadSafeDict for API compatibility; no locking is needed because
    asyncio is single-threaded and all dict mutations happen between await points.
    """

    __slots__ = ("_data",)

    def __init__(self, initial_data: Optional[Dict[K, V]] = None) -> None:
        self._data: Dict[K, V] = {} if initial_data is None else initial_data

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __setitem__(self, key: K, value: V) -> None:
        self._data[key] = value

    def __delitem__(self, key: K) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._data.copy())

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:
        return self._data.get(key, default)

    def get_or_set(self, key: K, default: Optional[V] = None) -> Optional[V]:
        v = self._data.get(key)
        if v is None:
            self._data.setdefault(key, default)
            return self._data.get(key)
        return v

    def pop(self, key: K, default: Optional[V] = None) -> Optional[V]:
        return self._data.pop(key, default)

    def setdefault(self, key: K, default: Optional[V] = None) -> V:
        return self._data.setdefault(key, default)

    def get_or_create(self, key: K, creator: Callable[..., V], *args, **kwargs) -> V:
        if key not in self._data:
            self._data[key] = creator(*args, **kwargs)
        return self._data[key]

    def update(
            self,
            m: Optional[Iterable[tuple[K, V]] | Mapping[K, V]] = None,
            /,
            **kwargs: V,
    ) -> None:
        if m is not None:
            self._data.update(m)
        if kwargs:
            self._data.update(kwargs)

    def clear(self) -> None:
        self._data.clear()

    def keys(self) -> KeysView[K]:
        return self._data.keys()

    def values(self) -> ValuesView[V]:
        return self._data.values()

    def items(self) -> ItemsView[K, V]:
        return self._data.items()

    def __str__(self) -> str:
        return str(self._data)

    def __repr__(self) -> str:
        return repr(self._data)
