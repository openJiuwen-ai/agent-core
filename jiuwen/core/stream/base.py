#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from enum import Enum
from typing import Dict, Any

from pydantic import BaseModel


class StreamMode(Enum):

    def __new__(cls, mode: str, desc: str, options: Dict[str, Any] = None):
        obj = object.__new__(cls)
        obj._value_ = mode
        obj.mode = mode
        obj.desc = desc
        obj.options = options or {}
        return obj

    def __str__(self):
        return f"StreamMode(mode={self.mode}, desc={self.desc}, options={self.options})"


class BaseStreamMode(StreamMode):
    OUTPUT = ("output", "Standard stream data defined by the framework")
    TRACE = ("trace", "Trace stream data produced by the graph")
    CUSTOM = ("custom", "Custom stream data defined by the runnable")


class OutputSchema(BaseModel):
    type: str
    index: int
    payload: Any


class TraceSchema(BaseModel):
    type: str
    payload: Any


class CustomSchema(BaseModel):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    class Config:
        arbitrary_types_allowed = True
        extra = "allow"
