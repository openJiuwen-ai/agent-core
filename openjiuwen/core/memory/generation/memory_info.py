#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from dataclasses import dataclass


@dataclass
class ExtractedDataType(Enum):
    USER = "user"


@dataclass
class ExtractedData(object):
    type: ExtractedDataType
    key: str
    value: str
