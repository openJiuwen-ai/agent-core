# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The one seam through which a candidate is ever executed.

The provider used to carry its own isolation (bubblewrap on Linux, seatbelt on
macOS), ported from the system this search came from. It is gone, for the same
reason the endpoint model client went: agent-core has its own sandbox — the
gateway-routed ``SysOperation`` — and a second, provider-local isolation path
is a bypass that eventually gets used.

What remains is the *shape* of an execution, owned here:

* stage a file tree (already path-validated by the caller),
* run one command in it with an explicit environment,
* read one result file back, tolerantly.

``execution_from_sys_operation`` realises that shape on the injected
``SysOperation``; tests realise it with a plain subprocess, which is honest
there — a test runs only text the test itself wrote.
"""

from __future__ import annotations

import asyncio
import shlex
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from openjiuwen.rsi.artifact_rsi.program_opt.logging_config import get_logger

log = get_logger("execution")


class ExecutionUnavailable(RuntimeError):
    """No way to execute a candidate was configured for this run."""


@dataclass(frozen=True)
class ExecutionOutcome:
    """What one staged run came back with.

    ``result_text`` is the result file's content when it exists and ``None``
    otherwise — absence is an ordinary outcome (the evaluator may have printed
    instead, or died), never an exception from the seam.
    """

    exit_code: Optional[int]
    output: str
    result_text: Optional[str]


#: ``(files, command, env, timeout_seconds, result_file) -> ExecutionOutcome``.
#: Synchronous by contract: the search runs on worker threads, and every
#: implementation owns its own bridge to wherever it actually executes.
EvaluationExecution = Callable[
    [Mapping[str, str], Sequence[str], Mapping[str, str], float, Optional[str]],
    ExecutionOutcome,
]


def execution_from_sys_operation(sys_operation: Any, loop: Any) -> EvaluationExecution:
    """agent-core's own sandbox, adapted to the seam.

    Each call stages into a fresh directory inside the sandbox instance —
    per-evaluation isolation on the cheap, while the instance itself (and
    whatever ``packages`` were provisioned into it) is reused across the run.

    **The directories are not swept, and that is the platform's call rather
    than an oversight.** ``fs`` has no delete at all, and the shell refuses
    ``rm -rf`` outright — "command rejected for safe". Both were tried against
    a real ``SysOperation``. Deletion is simply not something an operation is
    given here, and finding a spelling that slips past the filter would be the
    same kind of bypass this module exists to remove. So scratch accumulates
    for the length of one run, bounded by it, and the container is the thing
    that gets reclaimed.
    The async gateway API is bridged the same way the model is: scheduled onto
    the loop that owns the operation and waited on from the worker thread.
    """
    if sys_operation is None:
        raise ExecutionUnavailable(
            "program optimization needs a SysOperation: AgentServer registers a "
            "SysOperationCard (mode=sandbox) and hands the provider "
            "Runner.resource_mgr.get_sys_operation(card_id)"
        )

    def execute(
        files: Mapping[str, str],
        command: Sequence[str],
        env: Mapping[str, str],
        timeout: float,
        result_file: Optional[str],
    ) -> ExecutionOutcome:
        scratch = f"evolve-{uuid.uuid4().hex}"

        async def run() -> ExecutionOutcome:
            fs = sys_operation.fs()
            shell = sys_operation.shell()
            # Staged concurrently — every evaluation re-stages its whole tree,
            # so serial round-trips would multiply per candidate. The gateway's
            # `prepend_newline` defaults to True, which would silently corrupt
            # every staged file's first line.
            written = await asyncio.gather(*(
                fs.write_file(f"{scratch}/{path}", text, prepend_newline=False)
                for path, text in files.items()
            ))
            # Checked, because these do not raise. A denied or failed write
            # returns a result carrying a non-zero code, and dropping it made
            # a staging failure arrive three steps later as "the evaluator
            # wrote neither a result file nor any output" — pointing the reader
            # at the evaluator for something that happened before it ran.
            # Measured against a real SysOperation whose sandbox root did not
            # contain the scratch path: every write, the command and the read
            # all failed, and the run reported an evaluator that said nothing.
            for path, result in zip(files, written):
                code = getattr(result, "code", 0)
                if code:
                    raise ExecutionUnavailable(
                        f"could not stage {path} into the sandbox: "
                        f"{getattr(result, 'message', '') or f'error {code}'}"
                    )
            completed = await shell.execute_cmd(
                shlex.join(command),
                cwd=scratch,
                timeout=max(1, int(timeout)),
                environment=dict(env),
            )
            data = getattr(completed, "data", completed)
            output = f"{getattr(data, 'stderr', '')}{getattr(data, 'stdout', '')}"
            result_text: Optional[str] = None
            if result_file is not None:
                try:
                    read = await fs.read_file(f"{scratch}/{result_file}")
                    content = getattr(getattr(read, "data", read), "content", None)
                    result_text = content if isinstance(content, str) else None
                except Exception:  # noqa: BLE001 - absence is an ordinary outcome
                    result_text = None
            return ExecutionOutcome(
                exit_code=getattr(data, "exit_code", None),
                output=output,
                result_text=result_text,
            )

        future = asyncio.run_coroutine_threadsafe(run(), loop)
        return future.result(timeout + 120)

    return execute


__all__ = [
    "EvaluationExecution",
    "ExecutionOutcome",
    "ExecutionUnavailable",
    "execution_from_sys_operation",
]
