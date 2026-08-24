"""Embedded personal-context Core runtime.

``PersonalContext`` owns the small amount of orchestration needed by the first embedded
version: one in-memory pipeline queue, one consumer, one scheduler coroutine
per enabled provider, and an atomic JSON cursor per provider.  There is no
database, transport, child process, or additional runtime class here.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextConfig, PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.context_graph import (
    build_context_graph,
    read_context_graph_page,
    search_context_graph,
)
from openjiuwen.harness.personal_context.context_pipeline import ContextPipelineService
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
from openjiuwen.harness.personal_context.fetch.browser_bookmarks import BrowserBookmarksFetchService
from openjiuwen.harness.personal_context.fetch.feishu import (
    FeishuFetchService,
    _lark_cli_auth_status,
    _lark_cli_begin_authorization,
    _lark_cli_finish_authorization,
    required_scopes_for_config,
)
from openjiuwen.harness.personal_context.fetch.github import GitHubFetchService
from openjiuwen.harness.personal_context.fetch.local_files import LocalFilesFetchService
from openjiuwen.harness.personal_context.fetch.toutiao_reader import ToutiaoReaderFetchService
from openjiuwen.harness.personal_context.fetch.zhihu_reader import ZhihuReaderFetchService
from openjiuwen.harness.personal_context.models import FetchBatch, PersonalContextStatus
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_QUEUE_CAPACITY = 8
_CURSOR_SCHEMA_VERSION = 1
_MAX_CURSOR_BYTES = 512 * 1024
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_AUTHORIZATION_FAILED = "Feishu authorization failed"
_AUTHORIZATION_STATUS_UNAVAILABLE = "Feishu authorization status is unavailable"

_PROVIDER_TYPES: dict[str, type[ContextFetchService]] = {
    "local_files": LocalFilesFetchService,
    "github": GitHubFetchService,
    "feishu": FeishuFetchService,
    "browser_bookmarks": BrowserBookmarksFetchService,
    "zhihu_reader": ZhihuReaderFetchService,
    "toutiao_reader": ToutiaoReaderFetchService,
}


def _error(status: StatusCode, message: str, *, cause: BaseException | None = None) -> BaseError:
    return build_error(status, error_msg=message, cause=cause)


def _state_error(message: str) -> BaseError:
    return _error(StatusCode.CONTEXT_PROACTIVE_STATE_INVALID, message)


def _file_error(message: str, *, cause: BaseException | None = None) -> BaseError:
    return _error(StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR, message, cause=cause)


def _fetch_error(message: str, *, cause: BaseException | None = None) -> BaseError:
    return _error(StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR, message, cause=cause)


def _safe_service_id(value: object) -> str:
    text = str(value)
    if not _SAFE_SEGMENT.fullmatch(text):
        raise _state_error("invalid fetch service id")
    return text


def _redact_text(value: object, *, limit: int = 512) -> str:
    """Return a bounded error message without URL query credentials."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    if "://" in text:
        # Keep the host/path while removing URL userinfo and query/fragment.
        for token in re.findall(r"https?://[^\s]+", text, flags=re.IGNORECASE):
            parsed = urlsplit(token.rstrip(".,;"))
            safe = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
            text = text.replace(token, safe)
    text = re.sub(
        r"(?i)(token|api[_ -]?key|password|secret|authorization|cookie|credential)\s*[:=]\s*[^,;\s]+",
        r"\1=<redacted>",
        text,
    )
    return text[:limit]


def _source_fingerprint(config: PersonalContextFetchServiceConfig) -> str:
    payload = {"provider": config.provider, "source": config.source}
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise _file_error("source configuration is not JSON serializable", cause=exc) from exc
    return hashlib.sha256(encoded).hexdigest()


def _assert_no_symlink_chain(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise _file_error("cursor path must not traverse a symlink")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _authorization_expiry_monotonic(expires_at: str, *, now: float) -> float:
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        remaining = (expires.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    except (TypeError, ValueError):
        remaining = 600.0
    return now + max(1.0, remaining)


def _authorization_result(
    *,
    required_scopes: tuple[str, ...],
    granted_scopes: set[str],
    task: asyncio.Task[None] | None,
    challenge: Mapping[str, object] | None,
    error: str | None,
) -> dict[str, object]:
    verification_url: str | None = None
    expires_at: str | None = None
    if task is not None and not task.done():
        state = "authorizing"
        if challenge is not None:
            raw_url = challenge.get("verification_url")
            raw_expiry = challenge.get("expires_at")
            verification_url = raw_url if isinstance(raw_url, str) else None
            expires_at = raw_expiry if isinstance(raw_expiry, str) else None
        result_error = None
    elif error is not None:
        state = "authorization_failed"
        result_error = error
    elif set(required_scopes).issubset(granted_scopes):
        state = "authorized"
        result_error = None
    elif granted_scopes:
        state = "authorization_required"
        result_error = None
    else:
        state = "not_authorized"
        result_error = None
    return {
        "provider": "feishu",
        "state": state,
        "verification_url": verification_url,
        "expires_at": expires_at,
        "error": result_error,
    }


class PersonalContext:
    """Core aggregate for the embedded personal-context runtime."""

    FetchServiceConfig = PersonalContextFetchServiceConfig
    Config = PersonalContextConfig
    Status = PersonalContextStatus
    Error = BaseError

    @staticmethod
    def _status_for_name(name: str) -> StatusCode:
        """Return a PersonalContext-owned status for Host error translation."""

        return getattr(StatusCode, name, StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID)

    def __init__(self, *, home: str | Path) -> None:
        self._home = Path(home).expanduser().resolve()
        self._state = "CREATED"
        self._state_lock = asyncio.Lock()
        self._activation_task: asyncio.Task[None] | None = None
        self._authorization_lock = asyncio.Lock()
        self._authorization_task: asyncio.Task[None] | None = None
        self._authorization_challenge: dict[str, object] | None = None
        self._authorization_error: str | None = None

        self._config: PersonalContextConfig | None = None
        self._last_error: dict[str, object] | None = None

        self._pipeline_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=_QUEUE_CAPACITY)
        self._pipeline_service: ContextPipelineService | None = None

        self._fetch_lock = asyncio.Lock()
        self._fetch_tasks: dict[str, asyncio.Task[None]] = {}
        self._fetch_stop_events: dict[str, asyncio.Event] = {}
        self._fetch_providers: dict[str, ContextFetchService] = {}
        self._manual_fetch_tasks: dict[str, asyncio.Task[None]] = {}
        self._fetch_running: set[str] = set()
        self._fetch_states: dict[str, str] = {}
        self._fetch_errors: dict[str, str] = {}

    async def set_configuration(self, config: PersonalContextConfig) -> None:
        """Set or replace the complete configuration while PersonalContext is stopped."""
        if not isinstance(config, PersonalContext.Config):
            raise _error(StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID, "config must be PersonalContext.Config")
        async with self._state_lock:
            if self._state in {"STARTING", "RUNNING", "STOPPING", "FAILED"}:
                raise _state_error("PersonalContext must be stopped before configuration changes")
            if self._config == config:
                if self._state == "CREATED":
                    self._state = "CONFIGURED"
                return
            await self._cancel_authorization(clear_error=True)
            self._config = config
            self._fetch_states = {service.service_id: "STOPPED" for service in config.fetch_services}
            self._fetch_errors = {}
            self._fetch_providers = {}
            self._fetch_tasks = {}
            self._fetch_stop_events = {}
            self._manual_fetch_tasks = {}
            self._fetch_running = set()
            self._last_error = None
            self._state = "CONFIGURED"

    async def _required_authorization_scopes(self, provider: str) -> tuple[str, ...]:
        if provider != "feishu":
            raise _state_error("provider authorization is only supported for feishu")
        config = self._config
        if config is None:
            raise _state_error("PersonalContext has not been configured")
        if not any(item.provider == provider for item in config.fetch_services):
            raise _state_error("feishu provider is not configured")
        return required_scopes_for_config(config)

    async def get_authorization_status(self, provider: str) -> dict[str, object]:
        """Read shared provider authorization state without starting authorization."""

        async with self._state_lock:
            required_scopes = await self._required_authorization_scopes(provider)
            async with self._authorization_lock:
                task = self._authorization_task
                challenge = self._authorization_challenge
                authorization_error = self._authorization_error
                if task is not None and task.done():
                    if authorization_error is None and not task.cancelled():
                        with contextlib.suppress(asyncio.CancelledError):
                            if task.exception() is not None:
                                authorization_error = _AUTHORIZATION_FAILED
                if task is not None and not task.done():
                    return _authorization_result(
                        required_scopes=required_scopes,
                        granted_scopes=set(),
                        task=task,
                        challenge=challenge,
                        error=authorization_error,
                    )
                if authorization_error is not None:
                    return _authorization_result(
                        required_scopes=required_scopes,
                        granted_scopes=set(),
                        task=task,
                        challenge=challenge,
                        error=authorization_error,
                    )
                try:
                    _ready, granted_scopes = await _lark_cli_auth_status(required_scopes)
                except Exception:
                    authorization_error = _AUTHORIZATION_STATUS_UNAVAILABLE
                    granted_scopes = set()
                return _authorization_result(
                    required_scopes=required_scopes,
                    granted_scopes=granted_scopes,
                    task=task,
                    challenge=challenge,
                    error=authorization_error,
                )

    async def authorize_provider(self, provider: str) -> dict[str, object]:
        """Begin or reuse user OAuth for a configured provider without exposing tokens."""

        async with self._state_lock:
            required_scopes = await self._required_authorization_scopes(provider)
            now = asyncio.get_running_loop().time()
            async with self._authorization_lock:
                task = self._authorization_task
                challenge = self._authorization_challenge
                if task is not None and not task.done():
                    return _authorization_result(
                        required_scopes=required_scopes,
                        granted_scopes=set(),
                        task=task,
                        challenge=challenge,
                        error=self._authorization_error,
                    )
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        task.exception()
                    self._authorization_task = None
                    self._authorization_challenge = None
                    task = None
                    challenge = None
                try:
                    _ready, granted_scopes = await _lark_cli_auth_status(required_scopes)
                except Exception:
                    self._authorization_error = _AUTHORIZATION_STATUS_UNAVAILABLE
                    return _authorization_result(
                        required_scopes=required_scopes,
                        granted_scopes=set(),
                        task=None,
                        challenge=None,
                        error=self._authorization_error,
                    )
                if set(required_scopes).issubset(granted_scopes):
                    self._authorization_error = None
                    return _authorization_result(
                        required_scopes=required_scopes,
                        granted_scopes=granted_scopes,
                        task=None,
                        challenge=None,
                        error=None,
                    )
                try:
                    device_code, verification_url, expires_at = await _lark_cli_begin_authorization(required_scopes)
                except Exception:
                    self._authorization_error = _AUTHORIZATION_FAILED
                    return _authorization_result(
                        required_scopes=required_scopes,
                        granted_scopes=granted_scopes,
                        task=None,
                        challenge=None,
                        error=self._authorization_error,
                    )
                expires_monotonic = _authorization_expiry_monotonic(expires_at, now=now)
                timeout_seconds = max(1.0, min(30.0 * 60.0, expires_monotonic - now))
                task = asyncio.create_task(
                    self._finish_authorization(device_code, timeout_seconds=timeout_seconds),
                    name="personal-context-feishu-authorization",
                )
                self._authorization_task = task
                self._authorization_challenge = {
                    "verification_url": verification_url,
                    "expires_at": expires_at,
                    "expires_monotonic": expires_monotonic,
                }
                self._authorization_error = None
                return _authorization_result(
                    required_scopes=required_scopes,
                    granted_scopes=granted_scopes,
                    task=task,
                    challenge=self._authorization_challenge,
                    error=None,
                )

    async def _finish_authorization(self, device_code: str, *, timeout_seconds: float) -> None:
        current_task = asyncio.current_task()
        update_error = False
        authorization_error: str | None = None
        try:
            await _lark_cli_finish_authorization(device_code, timeout_seconds=timeout_seconds)
        except Exception:
            update_error = True
            authorization_error = _AUTHORIZATION_FAILED
        else:
            update_error = True
        finally:
            async with self._authorization_lock:
                if self._authorization_task is current_task:
                    self._authorization_task = None
                    self._authorization_challenge = None
                    if update_error:
                        self._authorization_error = authorization_error

    async def get_graph(self) -> dict[str, object]:
        """Read the last published Context graph without starting the runtime."""

        return await asyncio.to_thread(build_context_graph, self._home)

    async def search_graph(self, query: str) -> dict[str, object]:
        """Search the last published Context pages without starting the runtime."""

        if not isinstance(query, str) or not query.strip():
            raise _state_error("query must be a non-empty string")
        return await asyncio.to_thread(search_context_graph, self._home, query.strip())

    async def get_graph_page(self, node_id: str) -> dict[str, object]:
        """Read one published Context page without starting the runtime."""

        return await asyncio.to_thread(read_context_graph_page, self._home, node_id)

    async def activate_runtime(self) -> None:
        """Start the one Pipeline and all enabled provider scheduler tasks."""
        async with self._state_lock:
            if self._config is None:
                raise _state_error("PersonalContext has not been configured")
            if not self._config.enabled:
                # Host normally does not call activate_runtime for a disabled
                # configuration.  Treat an accidental call as a safe no-op.
                return
            if self._state == "RUNNING":
                return
            if self._state == "STARTING" and self._activation_task is not None:
                task = self._activation_task
            elif self._state in {"CONFIGURED", "STOPPED"}:
                self._state = "STARTING"
                task = asyncio.create_task(self._activate_runtime_impl(), name="personal-context-activation")
                self._activation_task = task
            else:
                raise _state_error(f"cannot activate PersonalContext from {self._state}")
        try:
            await asyncio.shield(task)
        finally:
            async with self._state_lock:
                if self._activation_task is task and task.done():
                    self._activation_task = None

    async def _activate_runtime_impl(self) -> None:
        config = self._config
        if config is None:
            raise _state_error("PersonalContext has not been configured")
        pipeline: ContextPipelineService | None = None
        try:
            # A stopped runtime never reuses its old queue.  The previous
            # pipeline has already drained or failed every item before the
            # lifecycle reaches STOPPED, so the fresh queue is the only queue
            # visible to the new consumer.
            self._pipeline_queue = asyncio.Queue(maxsize=_QUEUE_CAPACITY)
            pipeline = ContextPipelineService(home=self._home, config=config, input_queue=self._pipeline_queue)
            await pipeline.start()
            self._pipeline_service = pipeline
            if config.fetching_enabled:
                for service in config.fetch_services:
                    if service.enabled:
                        await self.start_fetch_service(service.service_id)
            async with self._state_lock:
                if self._state != "STARTING":
                    raise _state_error("PersonalContext activation was superseded")
                self._state = "RUNNING"
        except asyncio.CancelledError:
            await self._cancel_runtime_after_activation_failure(pipeline)
            async with self._state_lock:
                self._state = "FAILED"
            raise
        except BaseError as exc:
            await self._cancel_runtime_after_activation_failure(pipeline)
            self._set_last_error(exc, operation="activate_runtime")
            async with self._state_lock:
                self._state = "FAILED"
            raise
        except Exception as exc:
            wrapped = _state_error("PersonalContext runtime activation failed")
            wrapped.__cause__ = exc
            await self._cancel_runtime_after_activation_failure(pipeline)
            self._set_last_error(wrapped, operation="activate_runtime")
            async with self._state_lock:
                self._state = "FAILED"
            raise wrapped from exc

    async def _cancel_runtime_after_activation_failure(self, pipeline: ContextPipelineService | None) -> None:
        async with self._fetch_lock:
            tasks = list(self._fetch_tasks.values())
            for event in self._fetch_stop_events.values():
                event.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
        await asyncio.sleep(0)
        async with self._fetch_lock:
            for service_id, task in list(self._fetch_tasks.items()):
                if task.done():
                    self._fetch_tasks.pop(service_id, None)
                    self._fetch_stop_events.pop(service_id, None)
                    self._fetch_providers.pop(service_id, None)
                    self._fetch_states[service_id] = "STOPPED"
                else:
                    self._fetch_states[service_id] = "FAILED"
        if pipeline is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pipeline.stop(timeout_seconds=1.0)
            pipeline_running = True
            with contextlib.suppress(Exception):
                pipeline_running = pipeline.is_running()
            if not pipeline_running:
                self._pipeline_service = None

    async def start_fetch_service(self, service_id: str) -> None:
        """Start one enabled provider scheduler without fetching immediately."""
        safe_id = _safe_service_id(service_id)
        async with self._fetch_lock:
            config = self._config
            if config is None:
                raise _state_error("PersonalContext has not been configured")
            if not config.fetching_enabled:
                self._fetch_states[safe_id] = "STOPPED"
                raise _state_error("PersonalContext fetching is disabled")
            if self._state not in {"STARTING", "RUNNING"}:
                raise _state_error("PersonalContext runtime is not active")
            service = next((item for item in config.fetch_services if item.service_id == safe_id), None)
            if service is None:
                raise _state_error("unknown fetch service")
            if not service.enabled:
                self._fetch_states[safe_id] = "STOPPED"
                raise _state_error("fetch service is disabled")
            existing = self._fetch_tasks.get(safe_id)
            if existing is not None and not existing.done():
                return
            self._fetch_states[safe_id] = "STARTING"
            try:
                provider = self._create_fetch_provider(service)
            except BaseError as exc:
                self._fetch_states[safe_id] = "FAILED"
                self._fetch_errors[safe_id] = _redact_text(exc)
                return
            except Exception as exc:
                self._fetch_states[safe_id] = "FAILED"
                self._fetch_errors[safe_id] = _redact_text(exc)
                return
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                self._run_fetch_service(safe_id, stop_event), name=f"personal-context-fetch-{safe_id}"
            )
            self._fetch_providers[safe_id] = provider
            self._fetch_stop_events[safe_id] = stop_event
            self._fetch_tasks[safe_id] = task
            self._fetch_states[safe_id] = "RUNNING"

    async def run_fetch(
        self,
        *,
        service_id: str | None = None,
    ) -> dict[str, object]:
        """Start managed manual fetch rounds and return after acceptance."""
        async with self._state_lock:
            config = self._config
            pipeline = self._pipeline_service
            if config is None:
                raise _state_error("PersonalContext has not been configured")
            if not config.enabled:
                raise _state_error("PersonalContext is disabled")
            if self._state != "RUNNING":
                raise _state_error("PersonalContext runtime is not running")
            try:
                pipeline_running = pipeline is not None and pipeline.is_running()
            except Exception:
                pipeline_running = False
            if not pipeline_running:
                raise _state_error("PersonalContext pipeline is not running")

            async with self._fetch_lock:
                services = {item.service_id: item for item in config.fetch_services}
                if service_id is None:
                    target_ids = sorted(item.service_id for item in config.fetch_services if item.enabled)
                    if not target_ids:
                        raise _state_error("PersonalContext has no enabled fetch service")
                else:
                    safe_id = _safe_service_id(service_id)
                    if safe_id not in services:
                        raise _state_error("unknown fetch service")
                    target_ids = [safe_id]

                busy = sorted(
                    target_id
                    for target_id in target_ids
                    if target_id in self._fetch_running or self._fetch_states.get(target_id) == "STOPPING"
                )
                if busy:
                    raise _state_error(f"fetch service is already running: {', '.join(busy)}")

                providers = {
                    target_id: self._fetch_providers.get(target_id) or self._create_fetch_provider(services[target_id])
                    for target_id in target_ids
                }
                created: dict[str, asyncio.Task[None]] = {}
                previous_states = {target_id: self._fetch_states.get(target_id, "STOPPED") for target_id in target_ids}
                try:
                    self._fetch_running.update(target_ids)
                    for target_id in target_ids:
                        task = asyncio.create_task(
                            self._run_manual_fetch_once(target_id, providers[target_id]),
                            name=f"personal-context-fetch-manual-{target_id}",
                        )
                        self._manual_fetch_tasks[target_id] = task
                        self._fetch_states[target_id] = "RUNNING"
                        created[target_id] = task
                except BaseException:
                    for task in created.values():
                        task.cancel()
                    for target_id in target_ids:
                        self._manual_fetch_tasks.pop(target_id, None)
                        self._fetch_running.discard(target_id)
                        self._fetch_states[target_id] = previous_states[target_id]
                    raise

        return {"state": "accepted", "service_ids": target_ids}

    async def stop_fetch_service(self, service_id: str, *, timeout_seconds: float = 30.0) -> None:
        """Stop one provider, aborting its active run if the deadline expires."""
        if timeout_seconds <= 0:
            raise _error(StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "timeout_seconds must be greater than zero")
        safe_id = _safe_service_id(service_id)
        async with self._fetch_lock:
            if self._config is None or safe_id not in {item.service_id for item in self._config.fetch_services}:
                raise _state_error("unknown fetch service")
            scheduled = self._fetch_tasks.get(safe_id)
            manual = self._manual_fetch_tasks.get(safe_id)
            if scheduled is not None and scheduled.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    scheduled.exception()
                if self._fetch_tasks.get(safe_id) is scheduled:
                    self._fetch_tasks.pop(safe_id, None)
                    self._fetch_stop_events.pop(safe_id, None)
                    self._fetch_providers.pop(safe_id, None)
                scheduled = None
            if manual is not None and manual.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    manual.exception()
                if self._manual_fetch_tasks.get(safe_id) is manual:
                    self._manual_fetch_tasks.pop(safe_id, None)
                manual = None
            tasks = list(dict.fromkeys(task for task in (scheduled, manual) if task is not None))
            event = self._fetch_stop_events.get(safe_id)
            if not tasks:
                self._fetch_running.discard(safe_id)
                self._fetch_states[safe_id] = "STOPPED"
                return
            self._fetch_states[safe_id] = "STOPPING"
            if event is not None:
                event.set()
        timed_out = False
        task_error: BaseException | None = None
        try:
            _done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
            if pending:
                timed_out = True
                for task in pending:
                    task.cancel()
                await asyncio.sleep(0)
            for task in tasks:
                if not task.done():
                    continue
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    task_error = task_error or exc
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.sleep(0)
                raise
        finally:
            async with self._fetch_lock:
                if scheduled is not None and scheduled.done() and self._fetch_tasks.get(safe_id) is scheduled:
                    self._fetch_tasks.pop(safe_id, None)
                    self._fetch_stop_events.pop(safe_id, None)
                    self._fetch_providers.pop(safe_id, None)
                if manual is not None and manual.done() and self._manual_fetch_tasks.get(safe_id) is manual:
                    self._manual_fetch_tasks.pop(safe_id, None)
                current_tasks: list[asyncio.Task[None]] = []
                for task in (
                    self._fetch_tasks.get(safe_id),
                    self._manual_fetch_tasks.get(safe_id),
                ):
                    if task is not None and not task.done():
                        current_tasks.append(task)
                if current_tasks:
                    self._fetch_states[safe_id] = "FAILED"
                else:
                    self._fetch_running.discard(safe_id)
                    self._fetch_states[safe_id] = "STOPPED"
        if timed_out:
            raise _error(StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "fetch service stop timed out")
        if task_error is not None:
            raise task_error

    async def snapshot(self) -> PersonalContextStatus:
        """Return a bounded, credential-free runtime snapshot."""
        async with self._state_lock:
            config = self._config
            state = self._state
            last_error = dict(self._last_error) if self._last_error is not None else None
        pipeline = self._pipeline_service
        try:
            pipeline_running = pipeline.is_running() if pipeline is not None else False
        except Exception:
            pipeline_running = False
        status_state = state
        if state == "RUNNING" and not pipeline_running:
            status_state = "FAILED"
            if last_error is None:
                last_error = {
                    "code": StatusCode.CONTEXT_PROACTIVE_STATE_INVALID.code,
                    "status": StatusCode.CONTEXT_PROACTIVE_STATE_INVALID.name,
                    "message": "PersonalContext pipeline stopped unexpectedly",
                    "operation": "snapshot",
                }
        context_root = self._home / "workspace" / "context"
        description = context_root / "description.md"
        context_ready = False
        try:
            context_ready = description.is_file() and bool(description.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeError):
            context_ready = False
        return PersonalContext.Status(
            configured=config is not None,
            enabled=bool(config.enabled) if config is not None else False,
            fetching_enabled=bool(config.fetching_enabled) if config is not None else False,
            state=status_state,
            pipeline_running=pipeline_running,
            pipeline_queue_size=self._pipeline_queue.qsize(),
            fetch_service_states=dict(self._fetch_states),
            fetch_service_errors=dict(self._fetch_errors),
            context_root=str(context_root),
            context_ready=context_ready,
            last_error=last_error,
        )

    async def deactivate_runtime(self, *, timeout_seconds: float = 30.0) -> None:
        """Stop all provider tasks and the single pipeline under one deadline."""
        if timeout_seconds <= 0:
            raise _error(StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "timeout_seconds must be greater than zero")
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        async with self._state_lock:
            if self._state == "CREATED":
                return
            await self._cancel_authorization()
            self._state = "STOPPING"
            activation = self._activation_task
        stop_error: BaseError | None = None
        if activation is not None and not activation.done():
            activation.cancel()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0:
                try:
                    await asyncio.wait_for(asyncio.shield(activation), timeout=remaining)
                except asyncio.TimeoutError:
                    stop_error = _error(
                        StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "PersonalContext activation stop timed out"
                    )
                    activation.cancel()
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        raise
                except Exception as exc:
                    stop_error = _state_error("PersonalContext activation failed while stopping")
                    stop_error.__cause__ = exc
            else:
                stop_error = _error(StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "PersonalContext stop timed out")
                activation.cancel()
        service_ids = sorted(set(self._fetch_tasks) | set(self._manual_fetch_tasks))
        if service_ids:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                stop_error = _error(StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "PersonalContext stop timed out")
            else:
                stop_tasks = [
                    asyncio.create_task(
                        self.stop_fetch_service(service_id, timeout_seconds=remaining),
                        name=f"personal-context-fetch-stop-{service_id}",
                    )
                    for service_id in service_ids
                ]
                try:
                    done, pending = await asyncio.wait(stop_tasks, timeout=remaining)
                except asyncio.CancelledError:
                    for task in stop_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*stop_tasks, return_exceptions=True)
                    raise
                if pending:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    stop_error = _error(StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "PersonalContext stop timed out")
                for task in done:
                    try:
                        task.result()
                    except BaseError as exc:
                        stop_error = stop_error or exc
        pipeline = self._pipeline_service
        if pipeline is not None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                stop_error = stop_error or _error(
                    StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "PersonalContext stop timed out"
                )
            else:
                pipeline_stop_task = asyncio.create_task(
                    pipeline.stop(timeout_seconds=remaining), name="personal-context-pipeline-stop"
                )
                try:
                    await asyncio.wait_for(asyncio.shield(pipeline_stop_task), timeout=remaining)
                except asyncio.TimeoutError:
                    pipeline_stop_task.cancel()
                    await asyncio.sleep(0)
                    if pipeline_stop_task.done():
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            pipeline_stop_task.exception()
                    stop_error = stop_error or _error(
                        StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "PersonalContext stop timed out"
                    )
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        pipeline_stop_task.cancel()
                        raise
                    stop_error = stop_error or _error(
                        StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, "pipeline stop was cancelled"
                    )
                except BaseError as exc:
                    stop_error = stop_error or exc
        pipeline_running = False
        if pipeline is not None:
            try:
                pipeline_running = pipeline.is_running()
            except Exception:
                # If the runtime cannot report its state, retain it so a
                # subsequent stop can still attempt cleanup.
                pipeline_running = True
        activation_running = activation is not None and not activation.done()
        if not pipeline_running:
            self._pipeline_service = None
        if not activation_running and self._activation_task is activation:
            self._activation_task = None
        remaining_tasks = [
            task for task in (*self._fetch_tasks.values(), *self._manual_fetch_tasks.values()) if not task.done()
        ]
        runtime_still_running = bool(remaining_tasks) or pipeline_running or activation_running
        async with self._state_lock:
            self._state = "FAILED" if stop_error is not None or runtime_still_running else "STOPPED"
            if stop_error is not None:
                self._set_last_error(stop_error, operation="deactivate_runtime")
            elif runtime_still_running:
                self._set_last_error(
                    _state_error("PersonalContext runtime stop did not complete"), operation="deactivate_runtime"
                )
        if stop_error is not None:
            raise stop_error

    async def _cancel_authorization(self, *, clear_error: bool = False) -> None:
        async with self._authorization_lock:
            task = self._authorization_task
            self._authorization_task = None
            self._authorization_challenge = None
            if clear_error:
                self._authorization_error = None
            if task is not None and not task.done():
                task.cancel()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _create_fetch_provider(self, config: PersonalContextFetchServiceConfig) -> ContextFetchService:
        provider_type = _PROVIDER_TYPES.get(config.provider)
        if provider_type is None:
            raise _error(StatusCode.CONTEXT_PROACTIVE_CONFIG_INVALID, "unsupported fetch provider")
        return provider_type(config, home=self._home)

    async def _run_fetch_service(self, service_id: str, stop_event: asyncio.Event) -> None:
        provider = self._fetch_providers[service_id]
        config = self._service_config(service_id)
        interval = config.interval_seconds
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                return
            async with self._fetch_lock:
                if service_id in self._fetch_running:
                    continue
                self._fetch_running.add(service_id)
                if self._fetch_states.get(service_id) != "STOPPING":
                    self._fetch_states[service_id] = "RUNNING"
            try:
                await self._run_fetch_once(service_id, provider)
            except Exception as exc:
                async with self._fetch_lock:
                    if self._fetch_states.get(service_id) != "STOPPING":
                        self._fetch_states[service_id] = "FAILED"
                    self._fetch_errors[service_id] = _redact_text(exc)
            else:
                async with self._fetch_lock:
                    if self._fetch_states.get(service_id) != "STOPPING":
                        self._fetch_states[service_id] = "RUNNING"
                        self._fetch_errors.pop(service_id, None)
            finally:
                async with self._fetch_lock:
                    self._fetch_running.discard(service_id)

    async def _run_manual_fetch_once(
        self,
        service_id: str,
        provider: ContextFetchService,
    ) -> None:
        try:
            await self._run_fetch_once(service_id, provider)
        except Exception as exc:
            async with self._fetch_lock:
                if self._fetch_states.get(service_id) != "STOPPING":
                    self._fetch_states[service_id] = "FAILED"
                self._fetch_errors[service_id] = _redact_text(exc)
        else:
            async with self._fetch_lock:
                if self._fetch_states.get(service_id) != "STOPPING":
                    scheduler = self._fetch_tasks.get(service_id)
                    self._fetch_states[service_id] = (
                        "RUNNING" if scheduler is not None and not scheduler.done() else "STOPPED"
                    )
                    self._fetch_errors.pop(service_id, None)
        finally:
            async with self._fetch_lock:
                current = asyncio.current_task()
                if self._manual_fetch_tasks.get(service_id) is current:
                    self._manual_fetch_tasks.pop(service_id, None)
                self._fetch_running.discard(service_id)

    async def _run_fetch_once(self, service_id: str, provider: ContextFetchService) -> None:
        config = self._service_config(service_id)
        old_cursor = self._read_cursor(service_id)
        run_id = uuid4().hex
        last_cursor: dict[str, object] | None = None
        saw_batch = False
        pipeline_work_enqueued = asyncio.Event()
        item_count = 0

        async def abort_run() -> None:
            if pipeline_work_enqueued.is_set():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._abort_pipeline_run(service_id, run_id)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await provider.abort_run(run_id=run_id)

        try:
            async for batch in provider.fetch(run_id=run_id, cursor=old_cursor):
                if not isinstance(batch, FetchBatch):
                    raise _fetch_error("provider yielded an invalid batch")
                saw_batch = True
                item_count += len(batch.items)
                if config.max_items_per_run is not None and item_count > config.max_items_per_run:
                    raise _fetch_error("fetch service exceeded max_items_per_run")
                # Providers emit an empty batch to advance a no-change cursor.
                # It still participates in commit_run, but must not wake the
                # Processing/Filesystem Agent pipeline with no work.
                if batch.items:
                    await self._submit_batch(service_id, run_id, batch, enqueued=pipeline_work_enqueued)
                last_cursor = dict(batch.next_cursor) if batch.next_cursor is not None else None
            if not saw_batch:
                raise _fetch_error("provider produced no batch")
            if pipeline_work_enqueued.is_set():
                await self._finish_pipeline_run(service_id, run_id)
            await provider.commit_run(run_id=run_id)
            self._write_cursor(service_id, last_cursor)
        except asyncio.CancelledError:
            await abort_run()
            raise
        except BaseError:
            await abort_run()
            raise
        except Exception as exc:
            await abort_run()
            raise _fetch_error("fetch run failed", cause=exc) from exc

    async def _submit_batch(
        self,
        service_id: str,
        run_id: str,
        batch: FetchBatch,
        *,
        enqueued: asyncio.Event | None = None,
    ) -> None:
        await self._submit_pipeline_event("batch", service_id, run_id, batch, enqueued=enqueued)

    async def _finish_pipeline_run(self, service_id: str, run_id: str) -> None:
        await self._submit_pipeline_event("finish", service_id, run_id, None)

    async def _abort_pipeline_run(self, service_id: str, run_id: str) -> None:
        await self._submit_pipeline_event("abort", service_id, run_id, None)

    async def _submit_pipeline_event(
        self,
        tag: str,
        service_id: str,
        run_id: str,
        payload: FetchBatch | None,
        *,
        enqueued: asyncio.Event | None = None,
    ) -> None:
        completion = asyncio.get_running_loop().create_future()
        item = (tag, service_id, run_id, payload, completion)
        try:
            await self._pipeline_queue.put(item)
            if enqueued is not None:
                enqueued.set()
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            if not completion.done():
                completion.cancel()
            raise

    def remove_fetch_cursor(self, service_id: str) -> bytes | None:
        """Remove one cursor and return its unmodified bytes for Host rollback."""

        safe_id = _safe_service_id(service_id)
        path = self._home / "state" / "cursors" / f"{safe_id}.json"
        _assert_no_symlink_chain(path)
        try:
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise _file_error("cursor path is not a file")
                payload = handle.read(_MAX_CURSOR_BYTES + 1)
        except FileNotFoundError:
            return None
        except BaseError:
            raise
        except Exception as exc:
            raise _file_error("cursor removal failed", cause=exc) from exc
        if len(payload) > _MAX_CURSOR_BYTES:
            raise _file_error("cursor file is too large")
        try:
            _assert_no_symlink_chain(path)
            current = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode):
                raise _file_error("cursor path is not a file")
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise _file_error("cursor file changed during removal")
            path.unlink()
        except BaseError:
            raise
        except Exception as exc:
            raise _file_error("cursor removal failed", cause=exc) from exc
        return payload

    def _delete_fetch_cursor_without_backup(self, service_id: str) -> None:
        safe_id = _safe_service_id(service_id)
        path = self._home / "state" / "cursors" / f"{safe_id}.json"
        _assert_no_symlink_chain(path)
        try:
            initial = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(initial.st_mode):
                raise _file_error("cursor path is not a file")
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise _file_error("cursor path is not a file")
            if (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino):
                raise _file_error("cursor file changed during deletion")
        except FileNotFoundError:
            return
        except BaseError:
            raise
        except Exception as exc:
            raise _file_error("cursor deletion failed", cause=exc) from exc
        try:
            _assert_no_symlink_chain(path)
            current = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode):
                raise _file_error("cursor path is not a file")
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise _file_error("cursor file changed during deletion")
            path.unlink()
        except FileNotFoundError:
            return
        except BaseError:
            raise
        except Exception as exc:
            raise _file_error("cursor deletion failed", cause=exc) from exc

    def restore_fetch_cursor(self, service_id: str, payload: bytes | None) -> None:
        """Atomically restore cursor bytes removed by the current Host transaction."""

        safe_id = _safe_service_id(service_id)
        if payload is None:
            self._delete_fetch_cursor_without_backup(safe_id)
            return
        path = self._home / "state" / "cursors" / f"{safe_id}.json"
        _assert_no_symlink_chain(path)
        if not isinstance(payload, bytes):
            raise _file_error("cursor restore payload must be bytes or null")
        if len(payload) > _MAX_CURSOR_BYTES:
            raise _file_error("cursor restore payload is too large")
        temporary: Path | None = None
        try:
            if path.exists() and not path.is_file():
                raise _file_error("cursor path is not a file")
            path.parent.mkdir(parents=True, exist_ok=True)
            _assert_no_symlink_chain(path)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except BaseError:
            raise
        except Exception as exc:
            raise _file_error("cursor restore failed", cause=exc) from exc
        finally:
            if temporary is not None:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()

    def _read_cursor(self, service_id: str) -> dict[str, object] | None:
        safe_id = _safe_service_id(service_id)
        config = self._service_config(safe_id)
        path = self._home / "state" / "cursors" / f"{safe_id}.json"
        _assert_no_symlink_chain(path)
        if not path.exists():
            return None
        try:
            raw = path.read_bytes()
            if len(raw) > _MAX_CURSOR_BYTES:
                raise ValueError("cursor file is too large")
            data = json.loads(raw.decode("utf-8"))
        except BaseError:
            raise
        except Exception as exc:
            raise _file_error("cursor file is invalid", cause=exc) from exc
        if not isinstance(data, dict) or data.get("schema_version") != _CURSOR_SCHEMA_VERSION:
            raise _file_error("cursor schema is unsupported")
        if data.get("service_id") != safe_id or data.get("provider") != config.provider:
            raise _file_error("cursor service identity does not match configuration")
        if data.get("source_fingerprint") != _source_fingerprint(config):
            return None
        cursor = data.get("cursor")
        if cursor is None:
            return None
        if not isinstance(cursor, Mapping):
            raise _file_error("cursor payload must be an object or null")
        return dict(cursor)

    def _write_cursor(self, service_id: str, cursor: dict[str, object] | None) -> None:
        safe_id = _safe_service_id(service_id)
        config = self._service_config(safe_id)
        if cursor is not None:
            if not isinstance(cursor, Mapping):
                raise _file_error("cursor payload must be an object or null")
            cursor_payload: object = dict(cursor)
        else:
            cursor_payload = None
        data = {
            "schema_version": _CURSOR_SCHEMA_VERSION,
            "service_id": safe_id,
            "provider": config.provider,
            "source_fingerprint": _source_fingerprint(config),
            "cursor": cursor_payload,
            "committed_at": _utc_now(),
        }
        try:
            encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            raise _file_error("cursor payload is not JSON serializable", cause=exc) from exc
        if len(encoded) > _MAX_CURSOR_BYTES:
            raise _file_error("cursor payload is too large")
        path = self._home / "state" / "cursors" / f"{safe_id}.json"
        _assert_no_symlink_chain(path)
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except BaseError:
            raise
        except Exception as exc:
            raise _file_error("cursor write failed", cause=exc) from exc
        finally:
            if temporary is not None:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()

    def _service_config(self, service_id: str) -> PersonalContextFetchServiceConfig:
        config = self._config
        if config is None:
            raise _state_error("PersonalContext has not been configured")
        for service in config.fetch_services:
            if service.service_id == service_id:
                return service
        raise _state_error("unknown fetch service")

    def _set_last_error(self, error: BaseError, *, operation: str) -> None:
        self._last_error = {
            "code": error.code,
            "status": error.status.name,
            "message": _redact_text(error.message),
            "operation": operation,
        }
