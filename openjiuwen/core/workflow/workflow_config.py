# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from enum import Enum
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field

from openjiuwen.core.runtime.state import Transformer


class ComponentAbility(Enum):
    INVOKE = ("invoke", "batch in, batch out")
    STREAM = ("stream", "batch in, stream out")
    COLLECT = ("collect", "stream in, batch out")
    TRANSFORM = ("transform", "stream in, stream out")

    def __init__(self, _ability_name: str, desc: str):
        self._ability_name = _ability_name
        self._desc = desc

    @property
    def ability_name(self):
        return self._ability_name

    @property
    def desc(self) -> str:
        return self._desc


class CompIOConfig(BaseModel):
    inputs_schema: Optional[Dict] = None
    outputs_schema: Optional[Dict] = None
    inputs_transformer: Optional[Transformer] = None
    outputs_transformer: Optional[Transformer] = None


class WorkflowMetadata(BaseModel):
    name: str = Field(default="")
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    version: str = Field(default="")
    description: str = Field(default="")


class NodeSpec(BaseModel):
    io_config: CompIOConfig
    stream_io_configs: CompIOConfig
    abilities: List[ComponentAbility] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    comp_configs: Dict[str, NodeSpec] = Field(default_factory=dict)
    stream_edges: Dict[str, list[str]] = Field(default_factory=dict)
    edges: Dict[str, list[str]] = Field(default_factory=dict)


class WorkflowInputsSchema(BaseModel):
    type: str = Field(default="")
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)


class WorkflowConfig(BaseModel):
    metadata: Optional[WorkflowMetadata] = Field(default_factory=WorkflowMetadata)
    spec: Optional[WorkflowSpec] = Field(default_factory=WorkflowSpec)
    workflow_inputs_schema: Optional[WorkflowInputsSchema] = Field(default_factory=WorkflowInputsSchema)
    workflow_max_nesting_depth: int = Field(default=5, ge=0, le=10)
