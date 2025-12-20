#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import TypeVar

Input = TypeVar('Input', contravariant=True)
Output = TypeVar('Output', contravariant=True)

MAX_RESULT_SIZE = 10 * 1024 * 1024
REQUEST_TIMEOUT = 60

# RestFul Res
ERR_CODE = "errCode"
ERR_MESSAGE = "errMessage"
RESTFUL_DATA = "data"
