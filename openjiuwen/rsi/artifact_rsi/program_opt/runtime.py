# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""What the program optimizer needs from the platform: a model and a sandbox.

Both are the things `ArtifactEngineRequest` does not spell out. `model_config`
is a reference the platform already knows how to resolve, and isolation is not
in the contract at all -- yet a program optimizer executes code a model wrote,
so it cannot run without one. This module is where both are answered, kept apart
from the provider so the provider reads as the contract and nothing else.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Optional


from openjiuwen.rsi.artifact_rsi.program_opt.completion import (
    CompletionUsage,
)
from openjiuwen.rsi.artifact_rsi.program_opt.sandbox import (
    SandboxCapability,
    detect_local_capability,
)

#: Ceiling for one mutation call.
#:
#: Not a style choice. A reasoning model bills hidden thought against the same
#: budget, and below this floor it spends the whole allowance thinking and
#: returns nothing -- observed at exactly 16001 of 16000 permitted tokens, six
#: times running. An empty reply then becomes a failed candidate, which reads as
#: a model that cannot write code rather than a ceiling set too low.
DEFAULT_MAX_TOKENS_PER_CALL = 32_000

#: Wall clock for one mutation call. A whole-program rewrite by a reasoning
#: model is minutes, not seconds.
DEFAULT_CALL_TIMEOUT_SECONDS = 900.0


class ModelConfigError(RuntimeError):
    """`model_config` could not be resolved into something callable."""


class SandboxUnavailable(RuntimeError):
    """No isolation backend, so no candidate may be executed."""


def require_sandbox(override: Optional[str] = None) -> SandboxCapability:
    """The isolation a candidate is executed under, or a refusal.

    **The contract has no sandbox field, and this is the one thing that cannot
    be defaulted away.** A program optimizer runs code a model wrote against an
    evaluator, dozens of times; without isolation a single candidate can read the
    task's own model key, reach the network, or write outside its scratch
    directory. Refusing is the only honest answer.

    Detected here rather than taken from the caller. ScienceDiscovery's control
    plane probes once and tells its sidecar, because probing twice is how two
    answers disagree -- but there the two are separate processes. In-process
    there is only one answer to have, so it is taken where it is used.

    `override` names a backend explicitly for a deployment that knows better
    than the probe (a container where bubblewrap needs `--disable-userns`, say).
    """
    capability = detect_local_capability() if not override else SandboxCapability(backend=override)
    if not capability.available:
        raise SandboxUnavailable(
            "no isolation backend is available, and a program optimizer will not execute "
            "model-written code without one. Install bubblewrap (bwrap) on Linux; macOS "
            "ships sandbox-exec."
        )
    return capability


def completion_factory_from_model(model: Any, loop: Any) -> Callable[..., Any]:
    """The engine's injection seam, filled by an initialized ``Model`` service.

    The contract hands the provider a process-local model instance and forbids
    it from resolving IDs or building clients — ``request.model.invoke(...)``
    is the whole permission. The engine's seam is synchronous and runs on
    worker threads, while ``Model.invoke`` is a coroutine, so every call is
    scheduled onto the loop that owns the model and waited on from the worker —
    the same bridge the event sink already crosses in the other direction.

    ``should_stop`` is honoured by abandoning the wait, not the call: the
    coroutine cannot be cancelled from here without racing the client, so the
    answer of a stopped call is dropped and the loop is left to finish it.
    """
    import concurrent.futures

    from openjiuwen.rsi.artifact_rsi.program_opt.completion import CompletionUsage

    def factory(
        spec: Any,
        on_usage: Optional[Callable[[CompletionUsage], None]],
        should_stop: Callable[[], bool],
    ) -> Callable[..., str]:
        max_tokens = int(getattr(spec, "max_tokens_per_call", 0) or DEFAULT_MAX_TOKENS_PER_CALL)
        timeout = float(getattr(spec, "options", {}).get("completion_timeout", DEFAULT_CALL_TIMEOUT_SECONDS))

        def complete(
            prompt: str,
            sink: Optional[Callable[[CompletionUsage], None]] = None,
            on_failure: Optional[Callable[[str], None]] = None,
        ) -> str:
            report = sink or on_usage
            future = asyncio.run_coroutine_threadsafe(
                model.invoke(prompt, max_tokens=max_tokens), loop,
            )
            deadline = time.monotonic() + timeout
            while True:
                try:
                    reply = future.result(timeout=1.0)
                    break
                except concurrent.futures.TimeoutError:
                    if should_stop is not None and should_stop():
                        return ""
                    if time.monotonic() > deadline:
                        if on_failure is not None:
                            on_failure(f"model call exceeded {timeout:.0f}s")
                        return ""
                except Exception as error:  # noqa: BLE001 - a failed call is a failed candidate
                    if on_failure is not None:
                        on_failure(str(error))
                    return ""

            content = getattr(reply, "content", "")
            if isinstance(content, list):
                content = "".join(part for part in content if isinstance(part, str))
            text = str(content or "")

            usage = getattr(reply, "usage_metadata", None)
            if report is not None and usage is not None:
                completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                total = completion_tokens + int(getattr(usage, "input_tokens", 0) or 0)
                # An empty reply at the output ceiling is a budget spent on
                # hidden thinking, not a model with nothing to say.
                capped = bool(max_tokens and completion_tokens >= max_tokens and not text.strip())
                report(CompletionUsage(total=total, completion=completion_tokens, capped=capped))
            return text

        return complete

    return factory


__all__ = [
    "DEFAULT_CALL_TIMEOUT_SECONDS",
    "DEFAULT_MAX_TOKENS_PER_CALL",
    "ModelConfigError",
    "SandboxUnavailable",
    "completion_factory_from_model",
    "require_sandbox",
]
