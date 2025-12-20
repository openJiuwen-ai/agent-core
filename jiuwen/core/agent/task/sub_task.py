#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, Optional, Union

from pydantic import BaseModel, Field, field_validator

from jiuwen.agent.common.enum import SubTaskType
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput


class SubTask(BaseModel):
    id: str = Field(default="")
    sub_task_type: SubTaskType = Field(default=SubTaskType.UNDEFINED)
    func_id: str = Field(default="")
    func_name: str = Field(default="")
    func_args: Any = Field(default_factory=dict)
    result: Optional[Union[str, dict]] = Field(default=None)
    sub_task_context: Any = Field(default=None)

    @field_validator('func_args', mode='before')
    @classmethod
    def validate_func_args(cls, v):
        if isinstance(v, InteractiveInput):
            return v
        elif isinstance(v, dict):
            return v
        else:
            return v
