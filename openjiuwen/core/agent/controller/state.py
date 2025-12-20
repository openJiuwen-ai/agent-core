#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
"""Controller State - Pure data structure, no dependencies"""

from dataclasses import dataclass, field
from typing import List, Optional
from openjiuwen.core.agent.task.task import Task


@dataclass
class ControllerState:
    """Controller state: manages interrupted tasks and component IDs"""
    interrupted_tasks: List[Task] = field(default_factory=list)

    def is_interrupted(self) -> bool:
        return len(self.interrupted_tasks) > 0

    def clear_all(self):
        self.interrupted_tasks.clear()

    def clear_interrupted_task(self, workflow_id: str):
        self.interrupted_tasks = [
            task for task in self.interrupted_tasks
            if not (task.input and task.input.target_id == workflow_id)
        ]

    def add_interrupted_task(self, task: Task, component_id: Optional[str] = None,
                             component_id_key: str = "interrupted_component_id"):
        if not task:
            return
        if component_id:
            if not task.metadata:
                task.metadata = {}
            task.metadata[component_id_key] = component_id

        workflow_id = task.input.target_id if task.input else None
        if workflow_id:
            self.clear_interrupted_task(workflow_id)
        self.interrupted_tasks.append(task)

    def get_interrupted_task(self, workflow_id: Optional[str] = None) -> Optional[Task]:
        if not self.interrupted_tasks:
            return None
        if workflow_id:
            for task in self.interrupted_tasks:
                if task.input and task.input.target_id:
                    if task.input.target_id == workflow_id:
                        return task
            # Fallback: match by name
            for task in self.interrupted_tasks:
                if task.input and task.input.target_name == workflow_id:
                    return task
            return None
        else:
            return self.interrupted_tasks[0] if self.interrupted_tasks else None

    def get_interrupted_component_id(self, workflow_id: Optional[str] = None,
                                     component_id_key: str = "interrupted_component_id") -> Optional[str]:
        task = self.get_interrupted_task(workflow_id)
        if task and task.metadata:
            return task.metadata.get(component_id_key)
        return None

