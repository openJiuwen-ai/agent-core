# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native L1 path extraction.

实现保留在 core 的 ``openjiuwen.harness.security.legacy_path_extract``；本模块
维持历史导入路径，行为逐字节不变。

注意：security 仓的 ``fileguard.path_extract`` 与本实现语义不等价（shell 规范化 /
literal 处理 / outbound 规则不同），**不可**用其替代本兼容实现。

本模块与 ``tiered_policy`` 共用 core 冻结版的路径工具集合，因此二者需要一起维护。
"""

from __future__ import annotations

from agent_shield.tool_permission.fileguard.file_tool_specs import FileAction as FileAction

from openjiuwen.harness.security.legacy_path_extract import (
    extract_accesses_native as extract_accesses_native,
)
from openjiuwen.harness.security.legacy_path_extract import (
    extract_path_aware_command_accesses as extract_path_aware_command_accesses,
)
from openjiuwen.harness.security.legacy_path_extract import (
    extract_shell_path_accesses as extract_shell_path_accesses,
)
