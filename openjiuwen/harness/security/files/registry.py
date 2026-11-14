# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Registry of file-oriented tools - re-export from agent_shield.

Implementation lives in agent-security
``agent_shield.tool_permission.fileguard.file_tool_specs``; this module keeps
the historical ``openjiuwen.harness.security.files.registry`` import path.

Note: the shared builtin table registers ``glob`` against ``path`` while the
legacy core table used ``pattern``; callers here rely on the shared registry.
"""

from __future__ import annotations

from agent_shield.tool_permission.fileguard.file_tool_specs import (
    FileAction as FileAction,
)
from agent_shield.tool_permission.fileguard.file_tool_specs import (
    FileToolSpec as FileToolSpec,
)
from agent_shield.tool_permission.fileguard.file_tool_specs import (
    lookup_file_tool_specs as lookup_file_tool_specs,
)
from agent_shield.tool_permission.fileguard.file_tool_specs import (
    register_file_tool as register_file_tool,
)
