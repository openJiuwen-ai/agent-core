# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field

from openjiuwen.core.common.schema.workflow_spec import WorkflowSpec
from openjiuwen.core.workflow import WorkflowCard


class WorkflowConfig(BaseModel):
    card: WorkflowCard
    spec: Optional[WorkflowSpec] = Field(default_factory=WorkflowSpec)
    workflow_max_nesting_depth: int = Field(default=5, ge=0, le=10)
