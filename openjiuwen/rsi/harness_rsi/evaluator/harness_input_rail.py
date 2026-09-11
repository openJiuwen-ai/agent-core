# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Keep evaluation outputs out of the bound, fingerprinted plugin package."""

import json
from pathlib import Path

from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail
from openjiuwen.harness.security.permission_engine.fileguard.file_guard import (
    FileGuardChecker,
    normalize_path_guard_config,
)


class HarnessInputRail(BaseInterruptRail):
    """Reject known plugin writes; the evaluator fingerprint remains the backstop."""

    def __init__(self, harness_path: str | Path, workspace: str | Path):
        super().__init__()
        self.workspace = str(workspace)
        self._checker = FileGuardChecker(normalize_path_guard_config(
            {"file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                "paths": [{
                    "path": str(Path(harness_path).expanduser().resolve()),
                    "read": "allow", "write": "deny", "exec": "allow",
                }],
            }},
            workspace_root=Path(workspace),
        ))

    async def before_tool_call(self, ctx):
        args = ctx.inputs.tool_args
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                return  # The native tool reports malformed arguments.
        if not isinstance(args, dict):
            return
        result = self._checker.evaluate(ctx.inputs.tool_name, args)
        if result is not None and result.is_denied:
            self._skip_tool(
                ctx, ctx.inputs.tool_call,
                "[HARNESS_INPUT_READ_ONLY] The loaded plugin is an evaluation input, "
                "not an output directory. This write was not executed. "
                f"Write the deliverable under `{self.workspace}` instead. "
                "Reading skills and using plugin tools remain allowed.",
            )
