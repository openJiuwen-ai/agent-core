# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-specific security helpers (permission narrowing, etc.)."""

from openjiuwen.agent_teams.security.narrowing import (
    format_base_permissions_for_desc,
    narrow_permissions,
)

__all__ = [
    "format_base_permissions_for_desc",
    "narrow_permissions",
]
