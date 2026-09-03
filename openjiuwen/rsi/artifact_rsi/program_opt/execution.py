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


async def _stage_and_run(
    sys_operation: Any,
    files: Mapping[str, str],
    command: Sequence[str],
    env: Mapping[str, str],
    timeout: float,
    result_file: Optional[str],
    prelude: Sequence[Sequence[str]] = (),
) -> ExecutionOutcome:
    """One evaluation inside one sandbox: stage, run, read back.

    Shared by both ways of getting a sandbox — the one injected for the whole
    run, and the one built per evaluation — because what an evaluation *is*
    does not depend on where the container came from.
    """
    scratch = f"evolve-{uuid.uuid4().hex}"
    fs = sys_operation.fs()
    shell = sys_operation.shell()

    # Staged concurrently — every evaluation re-stages its whole tree, so
    # serial round-trips would multiply per candidate. The gateway's
    # `prepend_newline` defaults to True, which would silently corrupt every
    # staged file's first line.
    written = await asyncio.gather(*(
        fs.write_file(f"{scratch}/{path}", text, prepend_newline=False)
        for path, text in files.items()
    ))
    # Checked, because these do not raise. A denied or failed write returns a
    # result carrying a non-zero code, and dropping it made a staging failure
    # arrive three steps later as "the evaluator wrote neither a result file
    # nor any output" — pointing the reader at the evaluator for something
    # that happened before it ran.
    for path, result in zip(files, written):
        code = getattr(result, "code", 0)
        if code:
            raise ExecutionUnavailable(
                f"could not stage {path} into the sandbox: "
                f"{getattr(result, 'message', '') or f'error {code}'}"
            )

    # A container built for this evaluation alone starts empty, so whatever the
    # run was promised has to be put back before the candidate runs. Nothing to
    # do when the sandbox is shared — it was provisioned once at run start.
    for step in prelude:
        await shell.execute_cmd(shlex.join(step), cwd=scratch,
                                timeout=max(1, int(timeout)), environment={})

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


def execution_from_sys_operation(sys_operation: Any, loop: Any) -> EvaluationExecution:
    """agent-core's own sandbox, one instance for the whole run.

    Each call stages into a fresh directory inside it — per-evaluation
    isolation on the cheap, while the instance itself (and whatever
    ``packages`` were provisioned into it) is reused. The async gateway API is
    bridged the same way the model is: scheduled onto the loop that owns the
    operation and waited on from the worker thread.

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

    def execute(
        files: Mapping[str, str],
        command: Sequence[str],
        env: Mapping[str, str],
        timeout: float,
        result_file: Optional[str],
    ) -> ExecutionOutcome:
        future = asyncio.run_coroutine_threadsafe(
            _stage_and_run(sys_operation, files, command, env, timeout, result_file), loop)
        return future.result(timeout + 120)

    return execute


def execution_from_sandbox_card(
    card: Any, loop: Any, prelude: Sequence[Sequence[str]] = (),
) -> EvaluationExecution:
    """A sandbox of its own for every evaluation, built and reclaimed here.

    The other factory reuses one container for a whole run; this one gives each
    evaluation a container nothing else has touched. What that buys is the only
    isolation the scratch directory cannot: a candidate that gets past the AST
    gate and writes outside its own directory reaches nothing but a container
    that is about to be thrown away.

    What it costs is worth saying plainly, because it is not small. Every
    evaluation pays for a container, and a fresh container has none of what
    ``packages`` put in the last one — so a card that declares packages pays
    for installing them again, per evaluation, and a run is dozens of
    evaluations. Ship the candidate runtime in the image and leave ``packages``
    empty and the cost is only the container.

    **Created here, reclaimed here.** Nothing else knows these containers
    exist — AgentServer registered one card, not the dozens this makes — so
    leaving one behind is a leak nobody else can find. The release is in a
    ``finally``, and its own failure is logged rather than raised: a container
    that outlives its evaluation is a problem for later, while a lost result is
    a problem now.
    """
    if card is None:
        raise ExecutionUnavailable(
            "per-evaluation sandboxes need a SysOperationCard (mode=sandbox) to build "
            "them from: it carries the gateway address, the credentials and the "
            "isolation strategy, none of which this provider may invent"
        )

    def execute(
        files: Mapping[str, str],
        command: Sequence[str],
        env: Mapping[str, str],
        timeout: float,
        result_file: Optional[str],
    ) -> ExecutionOutcome:
        async def run() -> ExecutionOutcome:
            from openjiuwen.core.sys_operation import SysOperation

            fresh, key = _card_for_one_evaluation(card)
            operation = SysOperation(fresh)
            try:
                return await _stage_and_run(
                    operation, files, command, env, timeout, result_file, prelude)
            finally:
                await _release(operation, key, card)

        future = asyncio.run_coroutine_threadsafe(run(), loop)
        return future.result(timeout + 120)

    return execute


def _card_for_one_evaluation(card: Any) -> tuple[Any, str]:
    """The same card, pointed at a container of its own.

    `CUSTOM` scope is what makes the identity ours to choose: the isolation key
    is then literal — no `{session_id}` to resolve — so the key that creates the
    container is the key that reclaims it.
    """
    from openjiuwen.core.sys_operation.config import ContainerScope

    identity = f"eval-{uuid.uuid4().hex[:16]}"
    fresh = card.model_copy(deep=True)
    fresh.id = f"{card.id}-{identity}"
    isolation = fresh.gateway_config.isolation
    isolation.container_scope = ContainerScope.CUSTOM
    isolation.custom_id = identity
    return fresh, identity


async def _release(operation: Any, identity: str, card: Any) -> None:
    """Hand the container back, by the key it was created under."""
    from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import (
        SandboxGatewayClient,
    )

    key = operation.isolation_key_template
    launcher = getattr(card.gateway_config, "launcher_config", None)
    on_stop = getattr(launcher, "on_stop", "delete") or "delete"
    try:
        await SandboxGatewayClient.release(key, on_stop=on_stop)
    except Exception as error:  # noqa: BLE001 - a leak is for later, a lost result is now
        log.warning("could not release the per-evaluation sandbox %s (%s): %s",
                    key, identity, error)


__all__ = [
    "EvaluationExecution",
    "execution_from_sandbox_card",
    "ExecutionOutcome",
    "ExecutionUnavailable",
    "execution_from_sys_operation",
]
