# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""What the program optimizer needs from the platform: a model and a sandbox.

Both are the things `ArtifactEngineRequest` does not spell out. `model_config`
is a reference the platform already knows how to resolve, and isolation is not
in the contract at all -- yet a program optimizer executes code a model wrote,
so it cannot run without one. This module is where both are answered, kept apart
from the provider so the provider reads as the contract and nothing else.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

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


def load_model_endpoint(model_config: str) -> dict[str, Any]:
    """Read `ArtifactEngineRequest.model_config` into an OpenAI-compatible endpoint.

    The reference is a path to the same YAML/JSON the rest of RSI uses --
    `model_client_config` mapping to `ModelClientConfig` -- so a task that
    already names a model for the harness names it the same way here. Read
    rather than resolved through `Model`: the search calls one endpoint with one
    prompt and reads one string back, and building the platform's full client
    would buy retry rails and streaming this path does not use.

    `${VAR}` in any string is expanded from the environment, which is how a key
    stays out of the file that names the model.
    """
    reference = str(model_config or "").strip()
    if not reference:
        raise ModelConfigError("model_config is required: the search cannot run without a model")

    path = Path(reference).expanduser().resolve()
    if not path.is_file():
        raise ModelConfigError(f"model_config not found: {reference}")

    try:
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    except Exception as error:  # noqa: BLE001 - the path is user-supplied
        raise ModelConfigError(f"failed to read model_config {path}: {error}") from error

    if not isinstance(raw, dict):
        raise ModelConfigError(f"model_config must decode to a mapping: {path}")

    model = raw.get("model", raw)
    client = model.get("model_client_config") if isinstance(model, dict) else None
    if not isinstance(client, dict):
        raise ModelConfigError("model_config must contain a mapping field: model_client_config")

    client = _expand_env(client)
    base = str(client.get("api_base") or "").rstrip("/")
    if not base:
        raise ModelConfigError("model_client_config.api_base is required")

    request = model.get("model_request_config") if isinstance(model, dict) else None
    request = _expand_env(request) if isinstance(request, dict) else {}

    return {
        # The engine posts to an absolute chat-completions URL. `api_base` in a
        # platform model config is conventionally the `/v1` root, so only the
        # path is appended — and a base that already carries either half is left
        # alone, because both spellings appear in real config files.
        "endpoint": _chat_completions_url(base),
        "token": str(client.get("api_key") or ""),
        "timeout": float(client.get("timeout") or DEFAULT_CALL_TIMEOUT_SECONDS),
        "max_tokens": int(request.get("max_tokens") or DEFAULT_MAX_TOKENS_PER_CALL),
        # Absent leaves it to the provider's own default. A reasoning model with
        # thinking left on spends forty times the tokens for a better candidate,
        # which is the task's trade to make and not this module's.
        "thinking": str(request.get("thinking") or ""),
    }


def _chat_completions_url(base: str) -> str:
    """`api_base` in whatever spelling a config used, as one absolute URL."""
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _expand_env(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: os.path.expandvars(value) if isinstance(value, str) else value
        for key, value in data.items()
    }


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


def completion_factory_for(endpoint: dict[str, Any]) -> Callable[..., Any]:
    """Adapt `load_model_endpoint`'s result to the engine's injection seam.

    The engine takes `(spec, on_usage, should_stop) -> (prompt, ...) -> str`, and
    builds the call itself so that a stop, a token count and an empty reply each
    mean something to it. All this does is put the endpoint in front of that.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.completion import (
        completion_for,
    )

    def factory(
        spec: Any,
        on_usage: Optional[Callable[[CompletionUsage], None]],
        should_stop: Callable[[], bool],
    ) -> Callable[..., str]:
        return completion_for(
            endpoint["endpoint"],
            endpoint["token"],
            # The spec's ceiling wins when the caller set one: it is per-run and
            # the endpoint's is per-deployment.
            max_tokens=int(getattr(spec, "max_tokens_per_call", 0) or endpoint["max_tokens"]),
            thinking=(getattr(spec, "thinking", "") or endpoint["thinking"]) or None,
            timeout=endpoint["timeout"],
            on_usage=on_usage,
            should_stop=should_stop,
        )

    return factory


__all__ = [
    "DEFAULT_CALL_TIMEOUT_SECONDS",
    "DEFAULT_MAX_TOKENS_PER_CALL",
    "ModelConfigError",
    "SandboxUnavailable",
    "completion_factory_for",
    "load_model_endpoint",
    "require_sandbox",
]
