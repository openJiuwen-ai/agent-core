# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Stable ordering for packages and extensions that do not depend on each other."""

import hashlib
from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


def order_key(identifier: str) -> str:
    """Return the sort key for an id that has no dependency edge.

    The key is the hex SHA-256 of the UTF-8 id. It does not use Python's
    per-process ``hash()``, so the same ids sort the same way after a restart.
    """
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def topological_order(
    nodes: Sequence[T],
    identifier: Callable[[T], str],
    dependencies: Callable[[T], Sequence[str]],
) -> tuple[list[T], list[str]]:
    """Order nodes so each dependency comes first.

    Dependencies that are not in ``nodes`` are ignored. The caller decides
    whether a missing dependency is an error. Nodes that only point at each
    other in a cycle are returned as the second list and are not ordered.
    Ready nodes are taken in ``order_key`` order, then by id.
    """
    ids = [identifier(node) for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("topological_order received duplicate node ids")
    pending = {identifier(node): node for node in nodes}
    indegree = dict.fromkeys(pending, 0)
    dependents: dict[str, list[str]] = {node_id: [] for node_id in pending}
    for node in nodes:
        node_id = identifier(node)
        for dependency_id in dependencies(node):
            if dependency_id == node_id:
                indegree[node_id] += 1
                continue
            if dependency_id not in pending:
                continue
            indegree[node_id] += 1
            dependents[dependency_id].append(node_id)

    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    ordered: list[T] = []
    while ready:
        ready.sort(key=lambda node_id: (order_key(node_id), node_id))
        current = ready.pop(0)
        ordered.append(pending[current])
        for child_id in dependents[current]:
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)

    cycle = [node_id for node_id, degree in indegree.items() if degree > 0]
    cycle.sort(key=lambda node_id: (order_key(node_id), node_id))
    return ordered, cycle
