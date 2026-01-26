# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC
from typing import Generic, TypeVar, Optional

from pydantic import BaseModel, Field

T = TypeVar('T')


class BaseResult(BaseModel, Generic[T], ABC):
    """BaseResult"""
    code: int = Field(..., description="Status code: 0 = success, non-0 = failure")
    message: str = Field(..., description="Message details")
    data: Optional[T] = Field(None, description="Business data (returned only on success)")

    class Config:
        arbitrary_types_allowed = True
