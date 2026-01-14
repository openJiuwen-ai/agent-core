#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved

from enum import Enum
from typing import Literal


class AgentType(str, Enum):
    LLM_AGENT = "llm_agent"
    WORKFLOW = "workflow"


class BuildStage(str, Enum):
    INITIAL = "initial"
    PROCESSING = "processing"
    COMPLETED = "completed"


class ProgressStage(str, Enum):
    INITIALIZING = "initializing"
    RESOURCE_RETRIEVING = "resource_retrieving"
    CLARIFYING = "clarifying"
    GENERATING = "generating"
    VALIDATING = "validating"
    CONVERTING = "converting"


class ProgressStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    WARNING = "warning"


AgentTypeLiteral = Literal["llm_agent", "workflow"]
