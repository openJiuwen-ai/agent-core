# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Prefer ``openjiuwen.harness.security.permission_engine.toolguard.tool_policy``."""

from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import *  # noqa: F403
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import (  # noqa: F401
    _parse_level,
)
