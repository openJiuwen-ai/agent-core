# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Enhanced BashTool with command semantics, smart truncation, and security."""
from __future__ import annotations

import os
import pathlib
import re
import time
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Optional,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import sys_operation_logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.shell.bash._output import (
    CommandOutput,
    render_partial_on_failure,
    render_tool_content,
)
from openjiuwen.harness.tools.shell.bash._permission import (
    check_permission,
    PermissionConfig,
    PermissionMode,
)
from openjiuwen.harness.tools.shell.bash._security import (
    check_injection,
    get_destructive_warning,
)
from openjiuwen.harness.tools.shell.bash._semantics import interpret_exit_code
from openjiuwen.core.session import get_current_session
from openjiuwen.harness.tools.filesystem import (
    _detect_and_record_deletions,
    _parse_rm_targets,
    _record_rm_targets_before_deletion,
)

# Matches sudo not already followed by -n / -En / --non-interactive
_SUDO_NEEDS_N_RE = re.compile(
    r"\bsudo\b(?!(?:\s+-[a-zA-Z]*n|\s+--non-interactive))(?=\s)"
)


def _make_sudo_noninteractive(command: str) -> str:
    """Inject -n into sudo calls so they fail fast instead of hanging for a password."""
    return _SUDO_NEEDS_N_RE.sub("sudo -n", command)


_VALID_SHELL_TYPES = frozenset({"auto", "cmd", "powershell", "bash", "sh"})


@dataclass(frozen=True)
class _BashInputs:
    """Parsed and clamped inputs for a BashTool invocation."""

    command: str
    timeout: int
    workdir: str
    run_in_background: bool
    max_output_chars: int
    shell_type: str
    description: str


class BashTool(Tool):
    """Shell command executor with semantic exit-code interpretation,
        smart output truncation, and injection detection.
    """

    def __init__(
            self,
            operation: SysOperation,
            language: str = "cn",
            permission_mode: str = "auto",
            deny_patterns: list[str] | None = None,
            allow_patterns: list[str] | None = None,
            agent_id: Optional[str] = None,
            **_kwargs: Any,
    ) -> None:
        super().__init__(build_tool_card("bash", "BashTool", language, agent_id=agent_id))
        self._operation = operation
        self._permission = PermissionConfig(
            mode=PermissionMode(permission_mode),
            deny_patterns=PermissionConfig.compile_patterns(deny_patterns),
            allow_patterns=PermissionConfig.compile_patterns(allow_patterns),
        )

    # ── input parsing ─────────────────────────────────────────

    @staticmethod
    def _resolve_timeout(raw_value: Any, default: int = 300) -> int:
        """Parse and validate a timeout value."""
        try:
            timeout = int(raw_value)
        except (TypeError, ValueError):
            timeout = default
        try:
            max_timeout = int(os.getenv("BASH_TOOL_MAX_TIMEOUT_SECONDS") or "3600")
        except ValueError:
            max_timeout = 3600
        max_timeout = max(1, max_timeout)
        return max(1, min(timeout, max_timeout))

    @staticmethod
    def _resolve_max_output_chars(raw_value: Any, default: int = 0) -> int:
        """Parse and validate a max_output_chars value. 0 means no limit."""
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = default
        if value == 0:
            return 0
        try:
            max_chars = int(os.getenv("BASH_TOOL_MAX_OUTPUT_CHARS") or "20000")
        except ValueError:
            max_chars = 20000
        max_chars = max(200, max_chars)
        return max(200, min(value, max_chars))

    @staticmethod
    def _parse_inputs(inputs: Dict[str, Any]) -> _BashInputs:
        """Parse and clamp tool inputs."""
        shell_type = inputs.get("shell_type", "auto")
        if shell_type not in _VALID_SHELL_TYPES:
            shell_type = "auto"
        return _BashInputs(
            command=_make_sudo_noninteractive((inputs.get("command") or "").strip()),
            timeout=BashTool._resolve_timeout(inputs.get("timeout", 300)),
            workdir=inputs.get("workdir", ""),
            run_in_background=bool(inputs.get("run_in_background", False)),
            max_output_chars=BashTool._resolve_max_output_chars(inputs.get("max_output_chars", 0)),
            shell_type=shell_type,
            description=inputs.get("description", ""),
        )

    # ── invoke ────────────────────────────────────────────────

    def _build_history_path(self, session: Any) -> str:
        from openjiuwen.core.sys_operation.cwd import get_cwd, get_workspace
        base_dir = get_workspace() or str(pathlib.Path(get_cwd()).expanduser().resolve())
        agent_id = (
            session.agent_id() if hasattr(session, "agent_id")
            else session.get_agent_id() if hasattr(session, "get_agent_id")
            else None
        ) or "default"
        return os.path.join(
            base_dir, ".agent_history",
            f"file_ops_{agent_id}_{session.get_session_id()}.json",
        )

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        from openjiuwen.core.sys_operation.cwd import get_cwd

        p = self._parse_inputs(inputs)

        if not p.command:
            return ToolOutput(success=False, error="command cannot be empty")

        if os.getenv("OPENJIUWEN_BASH_STRICT") == "1":
            guard = self._guard(p)
            if guard is not None:
                return guard

        current_cwd = get_cwd()
        resolved_cwd = p.workdir or current_cwd

        if p.workdir and not os.path.isdir(resolved_cwd):
            return ToolOutput(success=False, error=f"workdir does not exist: {resolved_cwd}")

        warning = get_destructive_warning(p.command)

        if p.description:
            sys_operation_logger.debug("BashTool: %s — %s", p.description, p.command)

        # ── background execution ──────────────────────────────
        if p.run_in_background:
            res = await self._operation.shell().execute_cmd_background(
                p.command, cwd=resolved_cwd, shell_type=p.shell_type,
            )
            if res.code != StatusCode.SUCCESS.code:
                return ToolOutput(success=False, error=res.message)
            return ToolOutput(success=True, data={"pid": res.data.pid, "status": "started"})

        # ── pre-execution: record explicit rm targets ─────────
        _session = get_current_session()
        _history_path: Optional[str] = None
        if _session is not None:
            _history_path = self._build_history_path(_session)
            rm_targets = _parse_rm_targets(p.command)
            if rm_targets:
                await _record_rm_targets_before_deletion(_history_path, rm_targets, self._operation)

        # ── normal execution ──────────────────────────────────
        res = await self._operation.shell().execute_cmd(
            p.command, cwd=resolved_cwd, timeout=p.timeout, shell_type=p.shell_type,
        )
        if res.code != StatusCode.SUCCESS.code:
            # A post-launch failure (e.g. timeout) still carries output collected
            # before the kill in res.data; surface it instead of dropping it.
            partial = None
            if res.data is not None:
                partial = render_partial_on_failure(
                    CommandOutput(
                        stdout=res.data.stdout or "",
                        stderr=res.data.stderr or "",
                        exit_code=res.data.exit_code if res.data.exit_code is not None else -1,
                        warning=warning,
                        max_output_chars=p.max_output_chars,
                    ),
                    res.message,
                )
            if partial is not None:
                return ToolOutput(success=False, data={"content": partial}, error=partial)
            return ToolOutput(success=False, error=res.message)

        exit_code = res.data.exit_code if res.data else -1
        stdout = (res.data.stdout or "") if res.data else ""
        stderr = (res.data.stderr or "") if res.data else ""

        meaning = interpret_exit_code(p.command, exit_code, stdout, stderr)

        # ── post-execution: cross-reference history for missed deletions ──
        if _history_path is not None and not meaning.is_error:
            await _detect_and_record_deletions(_history_path)

        content, is_error = render_tool_content(
            CommandOutput(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                warning=warning,
                max_output_chars=p.max_output_chars,
            ),
            meaning.is_error,
        )
        return ToolOutput(
            success=not is_error,
            data={"content": content},
            error=content if is_error else None,
        )

    # ── stream ────────────────────────────────────────────────

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[ToolOutput]:
        from openjiuwen.core.sys_operation.cwd import get_cwd

        p = self._parse_inputs(inputs)

        if not p.command:
            yield ToolOutput(success=False, error="command cannot be empty")
            return

        current_cwd = get_cwd()
        resolved_cwd = p.workdir or current_cwd

        if os.getenv("OPENJIUWEN_BASH_STRICT") == "1":
            guard = self._guard(p)
            if guard is not None:
                yield guard
                return

        warning = get_destructive_warning(p.command)

        if p.description:
            sys_operation_logger.debug("BashTool(stream): %s — %s", p.description, p.command)

        # ── pre-execution: record explicit rm targets ─────────
        _session = get_current_session()
        _history_path: Optional[str] = None
        if _session is not None:
            _history_path = self._build_history_path(_session)
            rm_targets = _parse_rm_targets(p.command)
            if rm_targets:
                await _record_rm_targets_before_deletion(_history_path, rm_targets, self._operation)

        start = time.monotonic()
        accumulated_stdout = ""
        accumulated_stderr = ""
        final_exit_code: int = -1

        async for chunk in self._operation.shell().execute_cmd_stream(
                p.command, cwd=resolved_cwd, timeout=p.timeout, shell_type=p.shell_type,
        ):
            if chunk.code != StatusCode.SUCCESS.code:
                yield ToolOutput(success=False, error=chunk.message)
                return

            data = chunk.data
            elapsed = round(time.monotonic() - start, 2)

            if data.exit_code is not None:
                final_exit_code = data.exit_code

            text = data.text or ""
            stream_type = data.type or "stdout"
            if stream_type == "stderr":
                accumulated_stderr += text
            else:
                accumulated_stdout += text

            yield ToolOutput(
                success=True,
                data={
                    "text": text,
                    "type": stream_type,
                    "chunk_index": data.chunk_index,
                    "exit_code": data.exit_code,
                    "elapsed_time_seconds": elapsed,
                },
            )

        # ── post-execution: cross-reference history for missed deletions ──
        meaning = interpret_exit_code(p.command, final_exit_code, accumulated_stdout, accumulated_stderr)
        if _history_path is not None and not meaning.is_error:
            await _detect_and_record_deletions(_history_path)

        content, is_error = render_tool_content(
            CommandOutput(
                stdout=accumulated_stdout,
                stderr=accumulated_stderr,
                exit_code=final_exit_code,
                warning=warning,
                max_output_chars=p.max_output_chars,
            ),
            meaning.is_error,
        )
        yield ToolOutput(
            success=not is_error,
            data={"content": content},
            error=content if is_error else None,
        )

    def _guard(self, p: _BashInputs):
        # tool-layer injection check (supplements sys_operation safety)
        sec = check_injection(p.command)
        if sec.blocked:
            return ToolOutput(success=False, error=sec.reason)

        # permission pipeline (deny/allow patterns + mode enforcement)
        perm = check_permission(p.command, self._permission)
        if not perm.allowed:
            return ToolOutput(success=False, error=perm.reason)
        return None
