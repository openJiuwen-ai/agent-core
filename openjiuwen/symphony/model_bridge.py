"""Bridge tree workers to their injected model's existing event loop.

Native Models must use an owner-loop binding, normally supplied by TreeEngine.
An isolated loop is retained only for independent invoke-compatible test doubles;
it must never own a native Model's process-shared HTTP connection pool.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

try:
    from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError
except ModuleNotFoundError:
    APIConnectionError = APIError = APITimeoutError = AuthenticationError = None

_MODEL_CLEANUP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class _LoopBoundModel:
    model: Any
    loop: asyncio.AbstractEventLoop


def _bind_model_to_loop(model: Any, loop: asyncio.AbstractEventLoop) -> Any:
    """Bind without creating a client, thread, or event loop; None stays None."""
    if model is None or isinstance(model, (_LoopBoundModel, _ModelBridge)):
        return model
    return _LoopBoundModel(model, loop)


def _unwrap_model(model: Any) -> Any:
    while isinstance(model, (_LoopBoundModel, _ModelBridge)):
        model = model.model
    return model


def _model_identity(model: Any) -> dict[str, Any]:
    """Stable, credential-free model/protocol identity for build signatures."""
    model = _unwrap_model(model)
    if model is None:
        return {"protocol": "none"}
    from openjiuwen.symphony.orchestration.model import model_identity

    identity = model_identity(model)
    if identity.get("model") is None:
        request = getattr(model, "model_config", None)
        identity["model"] = str(getattr(request, "model", None) or getattr(model, "model_name", "") or "")
    return {
        **identity,
        "protocol": "core-model.invoke.v1",
        "implementation": f"{type(model).__module__}.{type(model).__qualname__}",
    }


class _BuildInterrupted(RuntimeError):
    """Cooperative cancellation and total task deadline failures."""


def _validate_model_binding(model: Any) -> None:
    from openjiuwen.core.foundation.llm import Model

    if isinstance(_unwrap_model(model), Model) and not isinstance(model, (_LoopBoundModel, _ModelBridge)):
        raise RuntimeError(
            "Native Model tree construction requires an owner event loop; "
            "use SymphonyRuntime.retrieval.build or an existing owner-loop model binding"
        )


def _error_chain(error: BaseException) -> list[BaseException]:
    pending = [error]
    seen: set[int] = set()
    chain: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        for attr in ("__cause__", "cause"):
            cause = getattr(current, attr, None)
            if isinstance(cause, BaseException):
                pending.append(cause)
    return chain


def _is_authentication_error(error: BaseException) -> bool:
    return any(
        (AuthenticationError is not None and isinstance(item, AuthenticationError))
        or getattr(item, "status_code", None) in {401, 403}
        for item in _error_chain(error)
    )


def _is_transient_model_error(error: BaseException) -> bool:
    if isinstance(error, _BuildInterrupted) or _is_authentication_error(error):
        return False
    chain = _error_chain(error)
    statuses = [getattr(item, "status_code", None) for item in chain]
    statuses = [status for status in statuses if isinstance(status, int)]
    if statuses:
        # Native wrappers repeat provider messages, so their text must never
        # override a permanent HTTP error deeper in the cause chain.
        return all(status in {408, 409, 429} or 500 <= status < 600 for status in statuses)
    for item in chain:
        api_error_types = (APITimeoutError, APIConnectionError, APIError)
        if any(error_type is not None and isinstance(item, error_type) for error_type in api_error_types):
            return True
        if isinstance(item, (TimeoutError, ConnectionError)):
            return True
        if any(marker in str(item).lower() for marker in ("timed out", "timeout")):
            return True
    return False


class _ModelBridge:
    def __init__(self, model: Any, *, check: Callable[[str], None] | None = None) -> None:
        _validate_model_binding(model)
        self.model = _unwrap_model(model)
        if not callable(getattr(self.model, "invoke", None)):
            raise TypeError("model must provide an invoke method")
        self._loop = (
            model.loop if isinstance(model, _LoopBoundModel)
            else model._loop if isinstance(model, _ModelBridge) else None
        )
        self._check = check
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cleanup_failure: _BuildInterrupted | None = None

    def _owner_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                ready = threading.Event()

                def run() -> None:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self._loop = loop
                    loop.call_soon(ready.set)
                    try:
                        loop.run_forever()
                    finally:
                        pending = asyncio.all_tasks(loop)
                        for task in pending:
                            task.cancel()
                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        loop.close()

                self._thread = threading.Thread(target=run, name="symphony-model-loop", daemon=True)
                self._thread.start()
                ready.wait()
            loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            raise RuntimeError("the injected model's event loop is not running")
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            raise RuntimeError("synchronous tree building must run outside the model's event loop")
        return loop

    def invoke(self, *, messages: Any, timeout: float, **kwargs: Any) -> Any:
        if self._cleanup_failure is not None:
            raise self._cleanup_failure
        if self._check is not None:
            self._check("model")
        loop = self._owner_loop()

        async def invoke() -> Any:
            result = self.model.invoke(messages=messages, timeout=timeout, **kwargs)
            return await result if inspect.isawaitable(result) else result

        future: concurrent.futures.Future = concurrent.futures.Future()
        completed = threading.Event()
        tasks: list[asyncio.Task] = []

        def finish(task: asyncio.Task) -> None:
            try:
                future.set_result(task.result())
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                completed.set()

        def start() -> None:
            task = loop.create_task(invoke())
            tasks.append(task)
            task.add_done_callback(finish)

        def cancel() -> None:
            for task in tasks:
                if not task.done():
                    task.cancel()

        loop.call_soon_threadsafe(start)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self._cleanup_failure is not None:
                    raise self._cleanup_failure
                if self._check is not None:
                    self._check("model")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"model invocation timed out after {timeout} seconds")
                try:
                    return future.result(timeout=min(0.05, remaining))
                except concurrent.futures.TimeoutError:
                    if future.done():
                        return future.result()
        except BaseException as exc:
            if not completed.is_set():
                try:
                    loop.call_soon_threadsafe(cancel)
                    cleaned = completed.wait(_MODEL_CLEANUP_TIMEOUT_SECONDS)
                except RuntimeError:
                    cleaned = False
                if not cleaned:
                    self._cleanup_failure = _BuildInterrupted(
                        "Model cancellation cleanup did not finish; tree construction stopped without retry"
                    )
                    raise self._cleanup_failure from exc
            raise

    def close(self) -> None:
        if self._thread is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            self._thread = None


@contextmanager
def _model_scope(model: Any, *, check: Callable[[str], None] | None = None):
    """Borrow a native owner loop, or own one isolated loop for test doubles."""
    if model is None or isinstance(model, _ModelBridge):
        yield model
        return
    bridge = _ModelBridge(model, check=check)
    try:
        yield bridge
    finally:
        bridge.close()


def _response_content(response: Any) -> str:
    content = response.get("content", "") if isinstance(response, dict) else getattr(response, "content", "")
    if isinstance(response, str):
        return response
    if isinstance(content, list):
        return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content)
    return str(content or "")


def _response_finish_reason(response: Any) -> str:
    return str(response.get("finish_reason", "") if isinstance(response, dict)
               else getattr(response, "finish_reason", ""))


def _response_usage(response: Any) -> tuple[int, int, int]:
    usage = response.get("usage_metadata") if isinstance(response, dict) else getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0, 0
    def value(name: str) -> int:
        return int((usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)) or 0)
    return value("input_tokens"), value("output_tokens"), value("total_tokens")
