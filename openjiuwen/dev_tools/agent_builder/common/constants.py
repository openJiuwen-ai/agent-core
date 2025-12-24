#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Final

# ========== Default Configuration ==========

DEFAULT_MAX_HISTORY_SIZE: Final[int] = 50

# ========== Regex Pattern ==========

JSON_EXTRACT_PATTERN: Final[str] = r"```(?:json)?\s*([\s\S]*?)\s*```"

# ========== Resource Type ==========

RESOURCE_TYPE_PLUGIN: Final[str] = "plugin"
