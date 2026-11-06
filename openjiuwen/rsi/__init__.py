# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Recursive self-improvement for standalone Expert Harnesses.

适配说明（program_opt + paper_opt）：本仓携带 program_opt / paper_opt 引擎及
伴生契约（schema/events/provider/request/usage）。harness_rsi 未随
「回合program产物rsi」提交进入本仓，相关导入仍裁剪；如后续需要，从上游
openJiuwen/agent-core 对应目录补齐后恢复导出即可。
"""

from openjiuwen.rsi.artifact_rsi import (
    ArtifactProvider,
    PaperArtifactProvider,
    ProgramArtifactProvider,
    build_request,
    validate_artifact_task_request,
)
from openjiuwen.rsi.artifact_rsi.program_opt import PuctProgramArtifactProvider

__all__ = [
    "ArtifactProvider",
    "PaperArtifactProvider",
    "ProgramArtifactProvider",
    "PuctProgramArtifactProvider",
    "build_request",
    "validate_artifact_task_request",
]
