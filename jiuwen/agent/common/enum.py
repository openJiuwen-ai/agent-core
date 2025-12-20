#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""enum constants"""

from enum import Enum, auto


class ControllerType(Enum):
    ReActController = "react"
    WorkflowController = "workflow"
    Undefined = "undefined"


class SubTaskType(Enum):
    PLUGIN = "plugin"
    WORKFLOW = "workflow"
    MCP = "mcp"
    UNDEFINED = "undefined"


class TaskStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    CANCELLED = auto()


class ReActControllerStatus(Enum):
    NORMAL = "NORMAL"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    TIMEOUT = "TIMEOUT"
