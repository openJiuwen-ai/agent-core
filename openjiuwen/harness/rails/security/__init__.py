# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""安全相关 Rails：提示层（``SecurityRail``）；工具执行层见 ``tool_security_rail``。"""

from openjiuwen.harness.rails.security.prompt_security_rail import SecurityRail
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail

__all__ = [
    "SecurityRail",
    "PermissionInterruptRail"
]