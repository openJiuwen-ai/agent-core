# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Task manager module.

This module implements the core functionality for task management, including:

- TaskManagerState: serializable snapshot of task manager state.
- TaskManager: manager responsible for task CRUD, state management, priority
  management, and hierarchical relationships.

Main features:
- Full CRUD for tasks.
- Task execution state management (``submitted``, ``working``, ``paused``,
  ``completed``, etc.).
- Priority management (querying and sorting by priority).
- Hierarchical relationship management (parent/child relationships).

Index structures:
- _priority_index: priority index for fast lookup by priority.
- _parent_to_children: parent → children index for fast child lookup.
- _child_to_parent: child → parent index for fast parent lookup.
- _root_tasks: set of root task IDs for fast root-level queries.
"""
from dataclasses import Field
from typing import Dict, Any, List, Union, Set, Optional
from collections import defaultdict

from pydantic.v1 import BaseModel

from openjiuwen.core.controller.schema.task import Task, TaskStatus


class TaskManagerState(BaseModel):
    """Serializable state of the task manager.

    Used to persist and restore the full state of the task manager.
    """
    tasks: Dict[str, Task]
    priority_index: Dict[int, List[str]]
    parent_to_children: Dict[str, Set[str]]
    children_to_parent: Dict[str, str]
    root_tasks: Set[str]


class TaskQuery(BaseModel):
    task_id: Optional[Union[str, List[str]]] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    priority: Optional[int] = None,
    status: Optional[TaskStatus] = None,
    with_children: bool = False,
    is_recursive: bool = False,
    is_root: bool = False


class TaskManager:
    """Task manager.

    Handles task CRUD operations, state transitions, priority updates and
    hierarchical relationships. Provides efficient index structures for
    querying tasks.
    """
    def __init__(self, config):
        """Initialize the task manager.

        Args:
            config: Agent configuration dictionary.
        """
        self._config: Dict[str, Any] = config
        self.tasks: Dict[str, Task] = {}  # task_id -> Task

        # ==================== Priority index ====================
        # Maps priority -> List[task_id]
        # Used for fast lookup and sorting by priority.
        self._priority_index: Dict[int, List[str]] = defaultdict(list)

        # ==================== Hierarchy indices ====================
        # Parent → children index: parent_task_id -> Set[child_task_id]
        # Used to quickly find all direct children of a task.
        self._parent_to_children: Dict[str, Set[str]] = defaultdict(set)

        # Child → parent index: child_task_id -> parent_task_id
        # Used to quickly find the parent task of a given task.
        self._child_to_parent: Dict[str, str] = {}

        # Root task set: contains IDs of all tasks without a parent.
        # Used to quickly query root tasks (e.g. ``is_root=True`` filters).
        self._root_tasks: Set[str] = set()

    def get_state(self) -> TaskManagerState:
        """Return a snapshot of task manager state.

        Returns:
            TaskManagerState: Snapshot of current task manager state.
        """
        return TaskManagerState(
            tasks=self.tasks,
            priority_index=self._priority_index,
            parent_to_children=self._parent_to_children,
            children_to_parent=self._child_to_parent,
            root_tasks=self._root_tasks
        )

    def load_state(self, state: TaskManagerState) -> None:
        """Restore task manager state from a snapshot.

        Args:
            state: Previously saved task manager state.
        """
        self.tasks = state.tasks
        self._priority_index = state.priority_index
        self._parent_to_children = state.parent_to_children
        self._child_to_parent = state.child_to_parent
        self._root_tasks = state.root_tasks

    # ==================== Task CRUD operations ====================
    def add_task(self, task: Union[Task, List[Task]]):
        """Add one or more tasks to the manager.

        Args:
            task: Single task or list of tasks to add.
        """
        self.tasks[task.task_id] = task
        ...

    def get_task(
            self,
            task_query: Optional[TaskQuery] = None
    ) -> List["Task"]:
        """Query tasks with flexible filters.

        Currently only basic querying by ``task_id`` is implemented.

        Args:
            task_query: Task query conditions.

        Returns:
            List[Task]: List of matching tasks.
        """
        # Query by task_id
        tasks = []
        if task_query.task_id is not None and isinstance(task_query.task_id, str) and task_query.task_id in self.tasks:
            tasks.append(self.tasks[task_query.task_idtask_id])
        # TODO: implement additional query filters (session, user, status, etc.)
        return tasks

    def pop_task(
            self,
            task_query: Optional[TaskQuery] = None
    ) -> List["Task"]:
        """Query and remove tasks.

        Behaves like ``get_task`` but also removes the returned tasks from the
        manager.

        Args:
            task_query: Task query conditions.

        Returns:
            List[Task]: Matching tasks that have been removed.
        """
        # Query by task_id
        tasks = []
        if task_query.task_id is not None and isinstance(task_query.task_id, str) and task_query.task_id in self.tasks:
            tasks.append(self.tasks[task_query.task_id])
            # Remove task
            del self.tasks[task_query.task_id]
            # TODO: remove task_id from other index structures.
        # TODO: additional removal/query logic.
        return tasks

    def update_task(self, task: Union[Task, List[Task]]):
        """Update one or more existing tasks.

        Updates task information without creating new tasks if they do not
        already exist.

        Args:
            task: Task instance or list of tasks to update.

        Returns:
            bool: Whether the update succeeded.
        """
        if isinstance(task, Task) and task.task_id in self.tasks:
            del self.tasks[task.task_id]
            self.tasks[task.task_id] = task
            # TODO: update related indices when a task changes.
        ...

    def remove_task(
            self,
            task_query: Optional[TaskQuery] = None
    ):
        """Remove tasks matching the given query.

        Supports deleting child tasks as well (to be implemented).

        Args:
            task_query: Task query conditions.
        """
        # Delete by task_id
        tasks = []
        if task_query.task_id is not None and isinstance(task_query.task_id, str) and task_query.task_id in self.tasks:
            # Remove task
            del self.tasks[task_query.task_id]
            # TODO: remove task_id from other index structures.
        # TODO: implement additional deletion logic.
        return tasks

    def get_child_task(
            self,
            task_id: Union[str, List[str]],
            is_recursive: bool = False,
    ) -> List[Task]:
        """Get child tasks of a given task.

        Args:
            task_id: Single task ID or list of task IDs.
            is_recursive: Whether to recursively fetch all descendants.

        Returns:
            List[Task]: List of child tasks.
        """
        tasks = []
        if isinstance(task_id, str) and task_id in self._parent_to_children:
            for child in self._parent_to_children[task_id]:
                tasks.append(self.tasks.get(child))
        # TODO: implement recursive traversal and list support.
        return tasks

    # ==================== Task status management ====================
    def update_task_status(
            self,
            task_id: Union[str, List[str]],
            new_status: "TaskStatus",
            with_children: bool = False,
            is_recursive: bool = False,
    ):
        """Update task status.

        Updates the status of one or more tasks and keeps indices in sync.

        Args:
            task_id: Single task ID or list of IDs.
            new_status: New task status.
            with_children: Whether to also update child tasks.
            is_recursive: Whether to recursively update all descendants.
        """
        if task_id is not None and isinstance(task_id, str) and task_id in self.tasks:
            task = self.pop_task(TaskQuery(task_id=task_id))
            task.status = new_status
            self.add_task(task)
        # TODO: implement support for lists and hierarchy propagation.
        ...

    # ==================== Task priority management ====================
    def set_priority(
            self,
            task_id: Union[str, List[str]],
            new_priority: str,
            with_children: bool = False,
            is_recursive: bool = False,
    ):
        """Set task priority.

        Updates task priority and keeps the priority index in sync.

        Args:
            task_id: Single task ID or list of IDs.
            new_priority: New priority value.
            with_children: Whether to also update child tasks.
            is_recursive: Whether to recursively update all descendants.
        """
        if task_id is not None and isinstance(task_id, str) and task_id in self.tasks:
            task = self.pop_task(TaskQuery(task_id=task_id))
            task.priority = new_priority
            self.add_task(task)
        # TODO: implement support for lists and hierarchy propagation.
        ...


