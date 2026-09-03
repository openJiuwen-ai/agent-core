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
from pathlib import Path
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


async def _stage_and_run(
    sys_operation: Any,
    files: Mapping[str, str],
    command: Sequence[str],
    env: Mapping[str, str],
    timeout: float,
    result_file: Optional[str],
    scratch_root: Optional[Path] = None,
) -> ExecutionOutcome:
    """One evaluation inside one sandbox: stage, run, read back.

    Shared by both ways of getting a sandbox — the one injected for the whole
    run, and the one built per evaluation — because what an evaluation *is*
    does not depend on where the container came from.
    """
    name = f"evolve-{uuid.uuid4().hex}"
    # Absolute when a root is given, because a relative path resolves against
    # agent-core's CWD context var and not against wherever the caller meant.
    scratch = str(scratch_root / name) if scratch_root is not None else name
    fs = sys_operation.fs()
    shell = sys_operation.shell()

    # Staged concurrently — every evaluation re-stages its whole tree, so
    # serial round-trips would multiply per candidate. The gateway's
    # `prepend_newline` defaults to True, which would silently corrupt every
    # staged file's first line.
    # `write_file` is the only thing in this API that creates a directory, so a
    # call that stages nothing would hand the command a `cwd` that does not
    # exist. That is not hypothetical: `probe_imports` runs one command and
    # stages no files, and the candidate-runtime probe came back "No such file
    # or directory" — reported as an execution environment missing numpy, on a
    # machine where numpy was installed.
    staged = dict(files) or {".evolve": "one evaluation's scratch directory\n"}
    written = await asyncio.gather(*(
        fs.write_file(f"{scratch}/{path}", text, prepend_newline=False)
        for path, text in staged.items()
    ))
    # Checked, because these do not raise. A denied or failed write returns a
    # result carrying a non-zero code, and dropping it made a staging failure
    # arrive three steps later as "the evaluator wrote neither a result file
    # nor any output" — pointing the reader at the evaluator for something
    # that happened before it ran.
    for path, result in zip(staged, written):
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
    # The gateway reports its *own* refusals at the result level, not through
    # the program's streams — a `code` and a sentence, with stdout empty. Two
    # very different things arrive that way and they must not be merged:
    #
    #   * the command never ran (rejected by the allowlist, denied a path).
    #     Every candidate would fail identically, so it is a run-level fault
    #     and is raised. Measured: a `sh` evaluator under the default LOCAL
    #     operation, whose allowlist has no `sh`, came back exit=-1 with empty
    #     output and read as "the evaluator printed nothing".
    #   * the command ran and was killed — a timeout, which is an ordinary
    #     property of a candidate and must stay one. It carries the signal in
    #     `exit_code` (-9), and raising on it would turn a slow candidate into
    #     a broken run.
    code = getattr(completed, "code", 0)
    if code:
        reason = getattr(completed, "message", "") or f"error {code}"
        if getattr(data, "exit_code", None) in (None, -1):
            raise ExecutionUnavailable(
                f"the execution environment refused `{shlex.join(command)}`: {reason}")
        output = f"{output}\n{reason}".strip()
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


def _bridge(operation: Any, loop: Any,
            scratch_root: Optional[Path] = None) -> EvaluationExecution:
    """The seam's synchronous face over an async ``SysOperation``.

    The search runs on worker threads and the gateway API is a coroutine, so
    every evaluation is scheduled onto the loop that owns the operation and
    waited on from the worker — the same bridge the model call already crosses
    in the other direction.
    """

    def execute(
        files: Mapping[str, str],
        command: Sequence[str],
        env: Mapping[str, str],
        timeout: float,
        result_file: Optional[str],
    ) -> ExecutionOutcome:
        future = asyncio.run_coroutine_threadsafe(
            _stage_and_run(operation, files, command, env, timeout, result_file,
                           scratch_root), loop)
        return future.result(timeout + 120)

    return execute


def execution_from_sys_operation(sys_operation: Any, loop: Any) -> EvaluationExecution:
    """agent-core's own sandbox, one instance for the whole run.

    Each call stages into a fresh directory inside it — per-evaluation
    isolation on the cheap, while the instance itself (and whatever
    ``packages`` were provisioned into it) is reused.

    **The directories are not swept, and that is the platform's call rather
    than an oversight.** ``fs`` has no delete at all, and the shell refuses
    ``rm -rf`` outright — "command rejected for safe". Both were tried against
    a real ``SysOperation``. Deletion is simply not something an operation is
    given here, and finding a spelling that slips past the filter would be the
    same kind of bypass this module exists to remove. So scratch accumulates
    for the length of one run, bounded by it, and the container is the thing
    that gets reclaimed.
    """
    if sys_operation is None:
        raise ExecutionUnavailable(
            "program optimization needs a SysOperation: AgentServer registers a "
            "SysOperationCard (mode=sandbox) and hands the provider "
            "Runner.resource_mgr.get_sys_operation(card_id)"
        )
    return _bridge(sys_operation, loop)


def local_execution(workspace: Any, loop: Any) -> EvaluationExecution:
    """Candidates run on this host, inside one directory, with no container.

    agent-core's own ``LOCAL`` SysOperation rather than a bare subprocess: it
    is the same `fs`/`shell` surface the gateway one has — so the staging, the
    result file and the timeouts are one implementation — and it still refuses
    a path outside ``workspace`` and a command matching its dangerous-pattern
    list. That is a boundary worth having, and it is not isolation. **A
    candidate is code a model wrote**, and on this path it runs as this
    process, with this process's reach. The AST gate narrows what a candidate
    may import; the module that used to provide the boundary is gone.

    This is what jiuwenswarm already does on a host without a sandbox service:
    its card builder falls back to ``OperationMode.LOCAL`` with the shell
    allowlist switched off entirely.

    Scratch directories are **absolute**, under ``workspace``. The relative
    ones the gateway path used resolve against agent-core's CWD context var,
    which a caller that never called ``init_cwd`` leaves pointing at the
    process's own directory — measured here as every write, the command and
    the read all refused for being outside the sandbox root.
    """
    from openjiuwen.core.sys_operation import (
        LocalWorkConfig,
        OperationMode,
        SysOperation,
        SysOperationCard,
    )

    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    operation = SysOperation(SysOperationCard(
        id=f"rsi-local-{uuid.uuid4().hex[:8]}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(sandbox_root=[str(root)], restrict_to_sandbox=True),
    ))

    return _bridge(operation, loop, scratch_root=root)


__all__ = [
    "EvaluationExecution",
    "ExecutionOutcome",
    "ExecutionUnavailable",
    "execution_from_sys_operation",
    "local_execution",
]
