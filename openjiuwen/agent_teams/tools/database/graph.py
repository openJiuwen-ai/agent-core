# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Dependency graph helper functions and constants."""

from typing import Dict, List, Optional

from openjiuwen.agent_teams.schema.status import TaskStatus


_TASK_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value})

_TASK_DEPENDENCY_REJECT_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.CLAIMED.value,
        TaskStatus.PLAN_APPROVED.value,
    }
)


def detect_cycle_in_adjacency(
    adjacency: Dict[str, List[str]],
) -> Optional[List[str]]:
    """Detect a cycle in a task-dependency adjacency map.

    The map points from a task to the tasks it depends on (``task_id ->
    [depends_on_task_id, ...]``). The walk follows edges in that
    direction; reaching an ancestor node in the current DFS path means
    the dependency chain loops back on itself.

    Args:
        adjacency: Outgoing-edge adjacency map.

    Returns:
        The cycle as a list of task IDs (the repeated node appears at
        both ends, e.g. ``[A, B, C, A]``), or ``None`` if the graph is
        acyclic. Iterative DFS with WHITE/GRAY/BLACK coloring keeps the
        recursion depth bounded for deep dependency chains.
    """
    white, gray, black = 0, 1, 2
    color: Dict[str, int] = {}
    for node, deps in adjacency.items():
        color[node] = white
        for dep in deps:
            color.setdefault(dep, white)

    cycle: Optional[List[str]] = None

    for root in list(color.keys()):
        if color[root] != white:
            continue
        path: List[str] = [root]
        color[root] = gray
        stack: List[tuple[str, List[str]]] = [(root, list(adjacency.get(root, ())))]
        while stack:
            node, children = stack[-1]
            if not children:
                stack.pop()
                color[node] = black
                path.pop()
                continue
            nxt = children.pop()
            c = color.get(nxt, white)
            if c == gray:
                idx = path.index(nxt)
                cycle = path[idx:] + [nxt]
                return cycle
            if c == white:
                color[nxt] = gray
                path.append(nxt)
                stack.append((nxt, list(adjacency.get(nxt, ()))))

    return None
