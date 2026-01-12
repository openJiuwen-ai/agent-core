# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any, Optional, Type, Union
from jsonschema import validate
from pydantic import BaseModel, Field
from openjiuwen.core.common.schema.card import BaseCard



class ToolInfo(BaseModel):
    type: str = Field(default="function")
    name: str = Field(default="")
    description: str = Field(default="")
    parameters: Union[Dict[str, Any], Type[BaseModel]] = Field(default_factory=dict)


class ToolCall(BaseModel):
    id: Optional[str]
    type: str
    name: str
    arguments: str
    index: Optional[int] = None
