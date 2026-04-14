# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Browser backend service with sticky sessions and guardrails."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import mimetypes
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import urlopen

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from .agents import build_browser_worker_agent
from .config import BrowserRunGuardrails, parse_command_args, resolve_playwright_mcp_cwd
from .profiles import BrowserProfile, BrowserProfileStore
from ..drivers.managed_browser import ManagedBrowserDriver, _default_chrome_user_data_dir
from ..utils.parsing import extract_json_object

MAX_ITERATION_MESSAGE = "Max iterations reached without completion"
_ctx_observer_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_observer_session_id",
    default="",
)
_ctx_observer_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "playwright_runtime_observer_request_id",
    default="",
)


@dataclass
class BrowserTaskProgressState:
    request_id: str = ""
    status: str = "unknown"
    completed_steps: list[str] = field(default_factory=list)
    remaining_steps: list[str] = field(default_factory=list)
    next_step: str = ""
    completion_evidence: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    recent_tool_steps: list[str] = field(default_factory=list)
    last_page_url: str = ""
    last_page_title: str = ""
    last_screenshot: Any = None
    last_worker_final: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "BrowserTaskProgressState":
        if not isinstance(data, dict):
            return cls()
        last_page = data.get("last_page") if isinstance(data.get("last_page"), dict) else {}
        return cls(
            request_id=str(data.get("request_id") or "").strip(),
            status=str(data.get("status") or "unknown").strip() or "unknown",
            completed_steps=[str(item).strip() for item in data.get("completed_steps") or [] if str(item).strip()],
            remaining_steps=[str(item).strip() for item in data.get("remaining_steps") or [] if str(item).strip()],
            next_step=str(data.get("next_step") or "").strip(),
            completion_evidence=[
                str(item).strip() for item in data.get("completion_evidence") or [] if str(item).strip()
            ],
            missing_requirements=[
                str(item).strip() for item in data.get("missing_requirements") or [] if str(item).strip()
            ],
            recent_tool_steps=[
                str(item).strip() for item in data.get("recent_tool_steps") or [] if str(item).strip()
            ],
            last_page_url=str(last_page.get("url") or "").strip(),
            last_page_title=str(last_page.get("title") or "").strip(),
            last_screenshot=data.get("last_screenshot"),
            last_worker_final=str(data.get("last_worker_final") or "").strip(),
        )

    def is_empty(self) -> bool:
        return (
            self.status == "unknown"
            and not self.completed_steps
            and not self.remaining_steps
            and not self.next_step
            and not self.completion_evidence
            and not self.missing_requirements
            and not self.recent_tool_steps
            and not self.last_page_url
            and not self.last_page_title
            and not self.last_worker_final
            and self.last_screenshot in (None, "")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "completed_steps": list(self.completed_steps),
            "remaining_steps": list(self.remaining_steps),
            "next_step": self.next_step or None,
            "completion_evidence": list(self.completion_evidence),
            "missing_requirements": list(self.missing_requirements),
            "recent_tool_steps": list(self.recent_tool_steps),
            "last_page": {
                "url": self.last_page_url,
                "title": self.last_page_title,
            },
            "last_screenshot": self.last_screenshot,
            "last_worker_final": self.last_worker_final or None,
            "request_id": self.request_id or None,
        }


class BrowserService:
    """Backend browser service with sticky logical sessions."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_base: str,
        model_name: str,
        mcp_cfg: McpServerConfig,
        guardrails: BrowserRunGuardrails,
        cancel_store: Optional[BaseKVStore] = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.mcp_cfg = mcp_cfg
        self.guardrails = guardrails
        self._cancel_store: BaseKVStore = cancel_store or InMemoryKVStore()

        self.started = False
        self._browser_agent: Optional[ReActAgent] = None
        self._locks: Dict[str, asyncio.Lock] = {}
        self._sessions: set[str] = set()
        self._inflight_tasks: Dict[str, set[asyncio.Task[Any]]] = {}
        self._screenshot_subdir = "screenshots"
        self._artifacts_subdir = "artifacts"
        self._mcp_cwd = self._resolve_mcp_cwd()
        self._screenshots_dir = self._mcp_cwd / self._screenshot_subdir
        self._artifacts_dir = self._mcp_cwd / self._artifacts_subdir
        self._profile_store = BrowserProfileStore(self._resolve_profile_store_path())
        self._profile_name = (os.getenv("BROWSER_PROFILE_NAME") or "jiuwenclaw").strip() or "jiuwenclaw"
        self._driver_mode = self._resolve_driver_mode()
        self._active_profile: Optional[BrowserProfile] = None
        self._managed_driver: Optional[ManagedBrowserDriver] = None
        self._registered_cdp_endpoint: str = ""
        self._failure_context_by_session: Dict[str, str] = {}
        self._progress_by_session: Dict[str, BrowserTaskProgressState] = {}
        self._heartbeat_task: Optional[asyncio.Task[Any]] = None
        self._heartbeat_interval: float = 30.0
        self._connection_healthy: bool = False
        self._last_heartbeat_ok: Optional[float] = None

    @property
    def browser_agent(self) -> Optional[ReActAgent]:
        return self._browser_agent

    @browser_agent.setter
    def browser_agent(self, value: Optional[ReActAgent]) -> None:
        self._browser_agent = value

    @property
    def artifacts_subdir(self) -> str:
        return self._artifacts_subdir

    @property
    def connection_healthy(self) -> bool:
        return self._connection_healthy

    @property
    def last_heartbeat_ok(self) -> Optional[float]:
        return self._last_heartbeat_ok

    def _resolve_profile_store_path(self) -> Path:
        configured = (os.getenv("BROWSER_PROFILE_STORE_PATH") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return self._mcp_cwd / ".browser" / "profiles.json"

    @staticmethod
    def _legacy_profile_store_path() -> Path:
        return Path.home() / ".jiuwenclaw" / "browser-move" / ".browser" / "profiles.json"

    def _iter_profile_store_paths(self) -> list[Path]:
        primary = self._resolve_profile_store_path()
        paths = [primary]
        configured = (os.getenv("BROWSER_PROFILE_STORE_PATH") or "").strip()
        if not configured:
            legacy = self._legacy_profile_store_path()
            if legacy.resolve() != primary.resolve():
                paths.append(legacy)
        return paths

    @staticmethod
    def _resolve_driver_mode() -> str:
        explicit = (os.getenv("BROWSER_DRIVER") or "").strip().lower()
        if explicit:
            if explicit not in {"remote", "managed", "extension"}:
                raise ValueError("BROWSER_DRIVER must be one of: remote, managed, extension")
            return explicit
        return "remote"

    @staticmethod
    def _cancel_key(session_id: str, request_id: Optional[str] = None) -> str:
        rid = (request_id or "").strip() or "*"
        return f"playwright_runtime:cancel:{session_id}:{rid}"

    @staticmethod
    def _inflight_key(session_id: str, request_id: Optional[str] = None) -> str:
        rid = (request_id or "").strip()
        return f"{session_id}:{rid}" if rid else session_id

    def _register_inflight_task(self, session_id: str, request_id: str, task: asyncio.Task[Any]) -> None:
        keys = (self._inflight_key(session_id), self._inflight_key(session_id, request_id))
        for key in keys:
            self._inflight_tasks.setdefault(key, set()).add(task)

    def _unregister_inflight_task(self, session_id: str, request_id: str, task: asyncio.Task[Any]) -> None:
        keys = (self._inflight_key(session_id), self._inflight_key(session_id, request_id))
        for key in keys:
            tasks = self._inflight_tasks.get(key)
            if not tasks:
                continue
            tasks.discard(task)
            if not tasks:
                self._inflight_tasks.pop(key, None)

    def _resolve_mcp_cwd(self) -> Path:
        params = getattr(self.mcp_cfg, "params", {}) or {}
        raw = str(params.get("cwd", "")).strip()
        if raw:
            return Path(raw).expanduser()
        return Path(resolve_playwright_mcp_cwd()).expanduser()

    @staticmethod
    def _is_cdp_endpoint_ready(endpoint: str) -> bool:
        base = str(endpoint or "").strip().rstrip("/")
        if not base:
            return False
        try:
            with urlopen(f"{base}/json/version", timeout=1.5) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
                if isinstance(payload, dict):
                    return bool(payload.get("webSocketDebuggerUrl") or payload.get("Browser"))
        except (OSError, ValueError):
            return False
        return False

    def _resolve_existing_cdp_profile(self) -> Optional[BrowserProfile]:
        candidates: list[BrowserProfile] = []
        for store_path in self._iter_profile_store_paths():
            store = BrowserProfileStore(store_path)
            selected = store.selected_profile()
            if selected is not None:
                candidates.append(selected)
            named = store.get_profile(self._profile_name)
            if named is not None and all(named.name != item.name for item in candidates):
                candidates.append(named)

        for profile in candidates:
            endpoint = str(profile.cdp_url or "").strip()
            if endpoint and self._is_cdp_endpoint_ready(endpoint):
                try:
                    self._profile_store.upsert_profile(profile, select=True)
                except Exception:
                    pass
                return profile
        return None

    def _should_replace_managed_driver(self, profile: BrowserProfile) -> bool:
        if self._managed_driver is None:
            return False
        if profile.driver_type != "managed":
            return True
        if self._active_profile is None:
            return True
        current_endpoint = str(self._active_profile.cdp_url or "").strip()
        target_endpoint = str(profile.cdp_url or "").strip()
        return current_endpoint != target_endpoint

    def _configured_cdp_endpoint(self) -> str:
        params = getattr(self.mcp_cfg, "params", {}) or {}
        env_map = dict(params.get("env", {}) or {})
        return str(env_map.get("PLAYWRIGHT_MCP_CDP_ENDPOINT") or "").strip()

    def _build_managed_profile(self) -> BrowserProfile:
        host = (os.getenv("BROWSER_MANAGED_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        port_raw = (os.getenv("BROWSER_MANAGED_PORT") or "9333").strip()
        try:
            port = int(port_raw)
            if port <= 0:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"Invalid BROWSER_MANAGED_PORT: {port_raw}") from exc

        kill_existing_raw = (os.getenv("BROWSER_MANAGED_KILL_EXISTING") or "").strip().lower()
        kill_existing = kill_existing_raw in {"1", "true", "yes", "on"}
        explicit_user_data_dir = (os.getenv("BROWSER_MANAGED_USER_DATA_DIR") or "").strip()
        if explicit_user_data_dir:
            user_data_dir = explicit_user_data_dir
        elif kill_existing:
            user_data_dir = _default_chrome_user_data_dir()
        else:
            user_data_dir = str(self._mcp_cwd / ".browser-profiles" / self._profile_name)
        browser_binary = (os.getenv("BROWSER_MANAGED_BINARY") or "").strip()
        extra_args = parse_command_args(os.getenv("BROWSER_MANAGED_ARGS") or "")
        cdp_url = f"http://{host}:{port}"
        return BrowserProfile(
            name=self._profile_name,
            driver_type="managed",
            cdp_url=cdp_url,
            browser_binary=browser_binary,
            user_data_dir=user_data_dir,
            debug_port=port,
            host=host,
            extra_args=extra_args,
        )

    def _inject_cdp_endpoint(self, endpoint: str) -> None:
        params = dict(getattr(self.mcp_cfg, "params", {}) or {})
        env_map = dict(params.get("env", {}) or {})
        env_map["PLAYWRIGHT_MCP_CDP_ENDPOINT"] = endpoint
        env_map.setdefault("PLAYWRIGHT_MCP_BROWSER", "chrome")
        env_map.pop("PLAYWRIGHT_MCP_DEVICE", None)
        params["env"] = env_map
        self.mcp_cfg.params = params

    async def _ensure_managed_driver_started(self) -> bool:
        if self._driver_mode != "managed":
            return False
        previous_endpoint = self._configured_cdp_endpoint()
        reusable_profile = self._resolve_existing_cdp_profile()
        if reusable_profile is not None:
            replaced_driver = False
            if self._should_replace_managed_driver(reusable_profile):
                await self._stop_managed_driver()
                replaced_driver = True
            self._active_profile = reusable_profile
            endpoint = str(reusable_profile.cdp_url or "").strip()
            self._inject_cdp_endpoint(endpoint)
            return replaced_driver or endpoint != previous_endpoint

        if self._managed_driver is not None:
            ready = await asyncio.to_thread(self._managed_driver.is_endpoint_ready)
            if ready:
                return False
            await self._stop_managed_driver()

        profile = self._profile_store.get_profile(self._profile_name)
        if (
            profile is None
            or profile.driver_type != "managed"
            or profile.debug_port <= 0
            or not str(profile.user_data_dir).strip()
        ):
            profile = self._build_managed_profile()
        configured_binary = (os.getenv("BROWSER_MANAGED_BINARY") or "").strip()
        if configured_binary:
            profile.browser_binary = configured_binary
        self._profile_store.upsert_profile(profile, select=True)
        self._active_profile = profile

        kill_existing_raw = (os.getenv("BROWSER_MANAGED_KILL_EXISTING") or "").strip().lower()
        kill_existing = kill_existing_raw in {"1", "true", "yes", "on"}

        driver = ManagedBrowserDriver(profile=profile)
        endpoint = await asyncio.to_thread(driver.start, 20.0, kill_existing)
        self._inject_cdp_endpoint(endpoint)
        profile.cdp_url = endpoint
        self._profile_store.upsert_profile(profile, select=True)
        self._managed_driver = driver
        return True

    async def _stop_managed_driver(self) -> None:
        if self._managed_driver is None:
            return
        driver = self._managed_driver
        self._managed_driver = None
        await asyncio.to_thread(driver.stop)

    def _ensure_screenshots_dir(self) -> None:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_local_screenshot_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        candidates: List[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend(
                [
                    self._mcp_cwd / path,
                    Path.cwd() / path,
                    path,
                ]
            )

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return path

    def _ensure_screenshot_in_folder(self, source_path: Path) -> Path:
        if not source_path.exists() or not source_path.is_file():
            return source_path

        self._ensure_screenshots_dir()
        try:
            source_resolved = source_path.resolve()
            target_dir_resolved = self._screenshots_dir.resolve()
            try:
                source_resolved.relative_to(target_dir_resolved)
                return source_resolved
            except ValueError:
                pass

            target_path = self._screenshots_dir / source_path.name
            if target_path.exists():
                target_resolved = target_path.resolve()
                if target_resolved != source_resolved:
                    target_path = self._screenshots_dir / (
                        f"{source_path.stem}-{uuid.uuid4().hex[:8]}{source_path.suffix}"
                    )

            shutil.copy2(source_path, target_path)
            return target_path
        except Exception:
            return source_path

    def _normalize_screenshot_value(self, screenshot: Any) -> Any:
        """Normalize screenshot for downstream multimodal APIs.

        - Keep remote URLs and existing data URLs as-is.
        - Ensure local screenshots are copied into screenshots/ folder.
        - Convert local image file paths to data URLs.
        """
        if screenshot is None or not isinstance(screenshot, str):
            return screenshot

        raw = screenshot.strip()
        if not raw:
            return None

        lowered = raw.lower()
        if lowered.startswith(("http://", "https://", "data:image/")):
            return raw

        local_path_str = raw[7:] if lowered.startswith("file://") else raw
        local_path = self._resolve_local_screenshot_path(local_path_str)
        if not local_path.exists() or not local_path.is_file():
            return raw
        local_path = self._ensure_screenshot_in_folder(local_path)

        mime_type, _ = mimetypes.guess_type(str(local_path))
        if not mime_type or not mime_type.startswith("image/"):
            return raw

        try:
            encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
        except Exception:
            return raw
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _is_retryable_transport_message(text: str) -> bool:
        lowered = str(text or "").lower()
        markers = (
            "session terminated",
            "not connected",
            "endofstream",
            "closedresourceerror",
            "brokenresourceerror",
            "stream closed",
            "connection closed",
            "broken pipe",
            "remoteprotocolerror",
            "readerror",
            "writeerror",
        )
        return any(marker in lowered for marker in markers)

    @classmethod
    def _is_retryable_transport_error(cls, exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        return cls._is_retryable_transport_message(name) or cls._is_retryable_transport_message(text)

    @staticmethod
    def _is_retryable_runtime_result(parsed: Dict[str, Any]) -> bool:
        if not isinstance(parsed, dict) or bool(parsed.get("ok", False)):
            return False
        text = (
            f"{parsed.get('error', '')}\n"
            f"{parsed.get('final', '')}"
        ).lower()
        markers = (
            "frame has been detached",
            "execution context was destroyed",
            "target page, context or browser has been closed",
            "target closed",
            "navigation failed because browser has disconnected",
            "context closed",
            "page crashed",
            "net::err_network_changed",
            "net::err_internet_disconnected",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _should_restart_after_runtime_result(parsed: Dict[str, Any]) -> bool:
        if not isinstance(parsed, dict):
            return False
        text = (
            f"{parsed.get('error', '')}\n"
            f"{parsed.get('final', '')}"
        ).lower()
        restart_markers = (
            "frame has been detached",
            "target page, context or browser has been closed",
            "target closed",
            "context closed",
            "page crashed",
        )
        return any(marker in text for marker in restart_markers)

    async def request_cancel(self, session_id: str, request_id: Optional[str] = None) -> None:
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required for cancellation")
        await self._cancel_store.set(self._cancel_key(sid, request_id), "1")

        request_id_clean = (request_id or "").strip()
        if request_id_clean:
            keys = [self._inflight_key(sid, request_id_clean)]
        else:
            keys = [self._inflight_key(sid)]

        for key in keys:
            for task in list(self._inflight_tasks.get(key, set())):
                if not task.done():
                    task.cancel()

    async def clear_cancel(self, session_id: str, request_id: Optional[str] = None) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        if request_id:
            await self._cancel_store.delete(self._cancel_key(sid, request_id))
            return
        await self._cancel_store.delete(self._cancel_key(sid, "*"))

    async def is_cancelled(self, session_id: str, request_id: Optional[str] = None) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        if request_id:
            exact = await self._cancel_store.get(self._cancel_key(sid, request_id))
            if exact is not None:
                return True
        wildcard = await self._cancel_store.get(self._cancel_key(sid, "*"))
        return wildcard is not None

    def session_new(self, session_id: Optional[str] = None) -> str:
        sid = (session_id or "").strip() or f"browser-{uuid.uuid4().hex}"
        self._sessions.add(sid)
        if sid not in self._locks:
            self._locks[sid] = asyncio.Lock()
        return sid

    async def ensure_runtime_ready(self) -> None:
        if self.started:
            browser_rebound = await self._ensure_managed_driver_started()
            configured_endpoint = self._configured_cdp_endpoint()
            if browser_rebound or configured_endpoint != self._registered_cdp_endpoint:
                await self._refresh_mcp_server_binding()
                self._browser_agent = None
            return

        if shutil.which("npx") is None:
            raise RuntimeError("npx not found in PATH. Install Node.js first.")

        from .browser_tools import ensure_browser_runtime_client_patch
        ensure_browser_runtime_client_patch()

        await self._ensure_managed_driver_started()
        self._ensure_screenshots_dir()
        await Runner.start()

        register_result = await Runner.resource_mgr.add_mcp_server(self.mcp_cfg, tag="browser.service")
        if register_result is not None and not getattr(register_result, "is_ok", lambda: False)():
            if hasattr(register_result, "error") and callable(register_result.error):
                error_value = register_result.error()
            elif hasattr(register_result, "msg") and callable(register_result.msg):
                error_value = register_result.msg()
            else:
                error_value = getattr(register_result, "value", register_result)
            if "already exist" not in str(error_value):
                raise RuntimeError(f"Failed to register Playwright MCP server: {error_value}")

        self._registered_cdp_endpoint = self._configured_cdp_endpoint()
        self.started = True
        self._start_heartbeat()

    async def ensure_started(self) -> None:
        await self.ensure_runtime_ready()
        if self._browser_agent is not None:
            return

        self._browser_agent = build_browser_worker_agent(
            provider=self.provider,
            api_key=self.api_key,
            api_base=self.api_base,
            model_name=self.model_name,
            mcp_cfg=self.mcp_cfg,
            max_steps=self.guardrails.max_steps,
            screenshot_subdir=self._screenshot_subdir,
            artifacts_subdir=self._artifacts_subdir,
            tool_result_observer=self._observe_worker_tool_result,
        )

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="browser-heartbeat"
            )

    async def _check_connection(self) -> None:
        from .browser_tools import get_registered_client
        if self._managed_driver is not None:
            driver_dict = getattr(self._managed_driver, "__dict__", {})
            endpoint_ready_fn = driver_dict.get("is_endpoint_ready")
            if endpoint_ready_fn is None:
                endpoint_ready_fn = driver_dict.get("_is_endpoint_ready")
            if endpoint_ready_fn is None:
                endpoint_ready_fn = getattr(self._managed_driver, "is_endpoint_ready", None)
            if endpoint_ready_fn is None:
                endpoint_ready_fn = getattr(self._managed_driver, "_is_endpoint_ready", None)
            ready = await asyncio.to_thread(endpoint_ready_fn)
            if not ready:
                raise RuntimeError("Chrome CDP endpoint not responding")
        server_id = (self.mcp_cfg.server_id or "").strip() or self.mcp_cfg.server_name
        client = get_registered_client(server_id)
        if client is None:
            raise RuntimeError("Playwright MCP client not found in registry")
        if not await client.ping():
            raise RuntimeError("Playwright MCP subprocess not responding")

    async def _heartbeat_loop(self) -> None:
        from openjiuwen.core.common.logging import logger as _logger
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self._check_connection()
                self._connection_healthy = True
                self._last_heartbeat_ok = asyncio.get_event_loop().time()
                _logger.info("BrowserService heartbeat: connection healthy")
            except Exception as exc:
                self._connection_healthy = False
                _logger.warning("BrowserService heartbeat: connection unhealthy — %s", exc)
                _logger.info(
                    "BrowserService heartbeat: restart deferred until the next browser task "
                    "to avoid reviving manually closed browsers during idle periods"
                )

    async def _restart(self) -> None:
        """Tear down and reinitialize the browser service (e.g. after stdio subprocess dies)."""
        await self._restart_browser_runtime()

    async def _remove_registered_mcp_server(self) -> None:
        server_resource_id = (self.mcp_cfg.server_id or "").strip() or self.mcp_cfg.server_name
        await Runner.resource_mgr.remove_mcp_server(
            server_id=server_resource_id,
            ignore_exception=True,
        )

    async def _refresh_mcp_server_binding(self) -> None:
        await self._remove_registered_mcp_server()
        register_result = await Runner.resource_mgr.add_mcp_server(self.mcp_cfg, tag="browser.service")
        if register_result is not None and not getattr(register_result, "is_ok", lambda: False)():
            if hasattr(register_result, "error") and callable(register_result.error):
                error_value = register_result.error()
            elif hasattr(register_result, "msg") and callable(register_result.msg):
                error_value = register_result.msg()
            else:
                error_value = getattr(register_result, "value", register_result)
            if "already exist" not in str(error_value):
                raise RuntimeError(f"Failed to refresh Playwright MCP server binding: {error_value}")
        self._registered_cdp_endpoint = self._configured_cdp_endpoint()

    async def _reset_browser_runtime(self) -> None:
        try:
            if self.started:
                await self._remove_registered_mcp_server()
        except Exception:
            pass
        self.started = False
        self._registered_cdp_endpoint = ""
        self._browser_agent = None
        await self._stop_managed_driver()

    async def _restart_browser_runtime(self) -> None:
        await self._reset_browser_runtime()
        await self.ensure_started()

    @staticmethod
    def _build_worker_conversation_id(session_id: str, request_id: str) -> str:
        sid = (session_id or "").strip() or "browser-session"
        rid = (request_id or "").strip() or "request"
        return f"{sid}:worker:{rid}:{uuid.uuid4().hex}"

    @classmethod
    def _clean_progress_items(cls, value: Any, *, limit: int) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            candidates = list(value)
        else:
            candidates = [value]
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            text_value = " ".join(str(item or "").split()).strip()
            if not text_value:
                continue
            lowered = text_value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(cls._trim_text(text_value, 220))
            if len(cleaned) >= limit:
                break
        return cleaned

    @staticmethod
    def _normalize_progress_status(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "complete": "completed",
            "completed": "completed",
            "done": "completed",
            "partial": "partial",
            "in_progress": "partial",
            "in-progress": "partial",
            "blocked": "blocked",
            "failed": "failed",
        }
        return aliases.get(normalized, "")

    @staticmethod
    def _push_recent_tool_step(existing: list[str], step: str, *, limit: int = 8) -> list[str]:
        normalized = " ".join(str(step or "").split()).strip()
        if not normalized:
            return list(existing)
        updated = [item for item in existing if item != normalized]
        updated.append(normalized)
        return updated[-limit:]

    @classmethod
    def _extract_page_snapshot(cls, value: Any) -> tuple[str, str]:
        if not isinstance(value, dict):
            return "", ""
        page = value.get("page") if isinstance(value.get("page"), dict) else {}
        url = str(value.get("url") or page.get("url") or "").strip()
        title = str(value.get("title") or page.get("title") or "").strip()
        return cls._trim_text(url, 240), cls._trim_text(title, 120)

    @staticmethod
    def _extract_screenshot_snapshot(value: Any) -> Any:
        if not isinstance(value, dict):
            return None
        screenshot = value.get("screenshot")
        if screenshot in (None, ""):
            return None
        return screenshot

    @classmethod
    def _summarize_observation_payload(cls, value: Any) -> str:
        if isinstance(value, dict):
            parts: list[str] = []
            error_text = str(value.get("error") or "").strip()
            if error_text:
                parts.append(f"error={cls._trim_text(error_text, 140)}")
            for key in ("message", "text", "result", "output", "value", "selector"):
                candidate = value.get(key)
                if candidate in (None, "") or isinstance(candidate, (dict, list, tuple, set)):
                    continue
                parts.append(cls._trim_text(str(candidate), 160))
                break
            url, title = cls._extract_page_snapshot(value)
            if url:
                parts.append(f"url={url}")
            if title:
                parts.append(f"title={title}")
            ok_value = value.get("ok")
            if ok_value is True and not parts:
                parts.append("ok")
            if not parts and value:
                parts.append(", ".join(list(value.keys())[:4]))
            return "; ".join(parts)
        if isinstance(value, (list, tuple)):
            nested = [cls._summarize_observation_payload(item) for item in list(value)[:2]]
            nested = [item for item in nested if item]
            return " | ".join(nested)
        text_value = " ".join(str(value or "").split()).strip()
        return cls._trim_text(text_value, 160)

    @classmethod
    def _summarize_tool_result(cls, tool_name: str, tool_result: Any) -> str:
        payload_summary = cls._summarize_observation_payload(tool_result) or "completed"
        if tool_name:
            return f"{tool_name}: {payload_summary}"
        return payload_summary

    def _get_progress_state(self, session_id: str) -> BrowserTaskProgressState:
        return self._progress_by_session.setdefault(session_id, BrowserTaskProgressState())

    def _update_progress_from_tool_observation(
        self,
        *,
        session_id: str,
        request_id: str,
        tool_name: str,
        tool_result: Any,
    ) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        progress_state = self._get_progress_state(sid)
        if request_id:
            progress_state.request_id = request_id
        progress_state.recent_tool_steps = self._push_recent_tool_step(
            progress_state.recent_tool_steps,
            self._summarize_tool_result(tool_name, tool_result),
        )
        url, title = self._extract_page_snapshot(tool_result)
        if url:
            progress_state.last_page_url = url
        if title:
            progress_state.last_page_title = title
        screenshot = self._extract_screenshot_snapshot(tool_result)
        if screenshot is not None:
            progress_state.last_screenshot = screenshot
        if progress_state.status == "unknown":
            progress_state.status = "partial"

    async def _observe_worker_tool_result(self, tool_name: str, tool_result: Any) -> None:
        session_id = _ctx_observer_session_id.get().strip()
        if not session_id:
            return
        request_id = _ctx_observer_request_id.get().strip()
        self._update_progress_from_tool_observation(
            session_id=session_id,
            request_id=request_id,
            tool_name=tool_name,
            tool_result=tool_result,
        )

    @classmethod
    def _should_treat_as_completed(cls, parsed: Dict[str, Any]) -> bool:
        status = cls._normalize_progress_status(
            parsed.get("status") or parsed.get("task_status")
        )
        if status != "completed":
            return False
        progress = parsed.get("progress") if isinstance(parsed.get("progress"), dict) else {}
        missing = cls._clean_progress_items(
            progress.get("missing_requirements") or parsed.get("missing_requirements"),
            limit=4,
        )
        evidence = cls._clean_progress_items(
            progress.get("completion_evidence") or parsed.get("completion_evidence"),
            limit=4,
        )
        final_text = str(parsed.get("final") or "").strip()
        return not missing and bool(evidence or final_text)

    def _update_progress_from_worker_result(
        self,
        *,
        session_id: str,
        request_id: str,
        parsed: Dict[str, Any],
    ) -> None:
        sid = (session_id or "").strip()
        if not sid or not isinstance(parsed, dict):
            return
        progress_state = self._get_progress_state(sid)
        if request_id:
            progress_state.request_id = request_id

        progress_payload = parsed.get("progress") if isinstance(parsed.get("progress"), dict) else {}
        status = self._normalize_progress_status(
            parsed.get("status") or parsed.get("task_status")
        )
        if not status:
            if bool(parsed.get("ok", False)):
                status = "completed"
            elif self._is_max_iteration_result(parsed):
                status = "partial"
            elif str(parsed.get("error") or "").strip():
                status = "failed"
            else:
                status = "partial"
        progress_state.status = status

        completed_steps = self._clean_progress_items(
            progress_payload.get("completed_steps") or parsed.get("completed_steps"),
            limit=8,
        )
        if completed_steps:
            progress_state.completed_steps = completed_steps

        remaining_steps = self._clean_progress_items(
            progress_payload.get("remaining_steps") or parsed.get("remaining_steps"),
            limit=8,
        )
        if remaining_steps:
            progress_state.remaining_steps = remaining_steps

        next_step = str(progress_payload.get("next_step") or parsed.get("next_step") or "").strip()
        if next_step:
            progress_state.next_step = self._trim_text(next_step, 220)

        completion_evidence = self._clean_progress_items(
            progress_payload.get("completion_evidence") or parsed.get("completion_evidence"),
            limit=6,
        )
        if completion_evidence:
            progress_state.completion_evidence = completion_evidence

        missing_requirements = self._clean_progress_items(
            progress_payload.get("missing_requirements") or parsed.get("missing_requirements"),
            limit=6,
        )
        if missing_requirements:
            progress_state.missing_requirements = missing_requirements

        url, title = self._extract_page_snapshot(parsed)
        if url:
            progress_state.last_page_url = url
        if title:
            progress_state.last_page_title = title
        screenshot = self._extract_screenshot_snapshot(parsed)
        if screenshot is not None:
            progress_state.last_screenshot = screenshot

        final_text = str(parsed.get("final") or "").strip()
        if final_text:
            progress_state.last_worker_final = self._trim_text(final_text, 1200)
            if bool(parsed.get("ok", False)) and not progress_state.completion_evidence:
                progress_state.completion_evidence = [self._trim_text(final_text, 220)]

    @classmethod
    def _build_progress_context(cls, progress_state: Optional[BrowserTaskProgressState]) -> str:
        if progress_state is None:
            return ""
        lines: list[str] = []
        if progress_state.status and progress_state.status != "unknown":
            lines.append(f"- Known progress status: {progress_state.status}")
        if progress_state.completed_steps:
            lines.append(f"- Completed steps: {' | '.join(progress_state.completed_steps)}")
        if progress_state.remaining_steps:
            lines.append(f"- Remaining steps: {' | '.join(progress_state.remaining_steps)}")
        if progress_state.next_step:
            lines.append(f"- Next step to try: {progress_state.next_step}")
        if progress_state.completion_evidence:
            lines.append(
                f"- Completion evidence observed: {' | '.join(progress_state.completion_evidence)}"
            )
        if progress_state.missing_requirements:
            lines.append(
                f"- Missing requirements / blockers: {' | '.join(progress_state.missing_requirements)}"
            )
        if progress_state.recent_tool_steps:
            lines.append("- Recent browser tool activity:")
            lines.extend(f"  - {step}" for step in progress_state.recent_tool_steps[-6:])
        if not lines:
            return ""
        return "Known progress for continuation:\n" + "\n".join(lines)

    @staticmethod
    def _is_empty_progress_state(progress_state: BrowserTaskProgressState) -> bool:
        return (
            progress_state.status == "unknown"
            and not any(
                (
                    progress_state.completed_steps,
                    progress_state.remaining_steps,
                    progress_state.next_step,
                    progress_state.completion_evidence,
                    progress_state.missing_requirements,
                    progress_state.recent_tool_steps,
                    progress_state.last_page_url,
                    progress_state.last_page_title,
                    progress_state.last_worker_final,
                )
            )
            and progress_state.last_screenshot in (None, "")
        )

    def _export_progress_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        progress_state = self._progress_by_session.get((session_id or "").strip())
        if progress_state is None:
            return None
        if self._is_empty_progress_state(progress_state):
            return None
        return progress_state.to_dict()

    @classmethod
    def build_progress_context(cls, progress_state: Optional[BrowserTaskProgressState]) -> str:
        return cls._build_progress_context(progress_state)

    def record_tool_progress(
        self,
        *,
        session_id: str,
        request_id: str,
        tool_name: str,
        tool_result: Any,
    ) -> None:
        self._update_progress_from_tool_observation(
            session_id=session_id,
            request_id=request_id,
            tool_name=tool_name,
            tool_result=tool_result,
        )

    @classmethod
    def should_treat_as_completed(cls, parsed: Dict[str, Any]) -> bool:
        return cls._should_treat_as_completed(parsed)

    def record_worker_progress(
        self,
        *,
        session_id: str,
        request_id: str,
        parsed: Dict[str, Any],
    ) -> None:
        self._update_progress_from_worker_result(
            session_id=session_id,
            request_id=request_id,
            parsed=parsed,
        )

    def get_progress_state(self, session_id: str) -> Optional[BrowserTaskProgressState]:
        return self._progress_by_session.get((session_id or "").strip())

    def export_progress_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._export_progress_state(session_id)

    def build_failure_summary(
        self,
        *,
        task: str,
        error: str,
        page_url: str,
        page_title: str,
        final: str,
        screenshot: Optional[str],
        attempt: int,
        progress_state: Optional[BrowserTaskProgressState],
    ) -> str:
        return self._build_failure_summary(
            task=task,
            error=error,
            page_url=page_url,
            page_title=page_title,
            final=final,
            screenshot=screenshot,
            attempt=attempt,
            progress_state=progress_state,
        )

    def set_progress_state(self, session_id: str, progress_state: BrowserTaskProgressState) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        if progress_state.is_empty():
            self._progress_by_session.pop(sid, None)
            return
        self._progress_by_session[sid] = progress_state

    def clear_progress_state(self, session_id: str) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        self._progress_by_session.pop(sid, None)

    async def _run_task_once(self, task: str, session_id: str, request_id: str) -> Dict[str, Any]:
        if self._browser_agent is None:
            raise RuntimeError("BrowserService is not started")

        task_prompt = (
            f"Session id: {session_id}\n"
            f"Request id: {request_id}\n"
            f"Max steps: {self.guardrails.max_steps}\n"
            f"Max failures: {self.guardrails.max_failures}\n\n"
            f"Task:\n{task}\n\n"
            "Perform the task in the current logical browser session/tab for this session id."
        )
        worker_conversation_id = self._build_worker_conversation_id(session_id, request_id)
        token_session = _ctx_observer_session_id.set(session_id)
        token_request = _ctx_observer_request_id.set(request_id)
        try:
            result = await Runner.run_agent(
                self._browser_agent,
                {"query": task_prompt, "conversation_id": worker_conversation_id, "request_id": request_id},
            )
        finally:
            _ctx_observer_session_id.reset(token_session)
            _ctx_observer_request_id.reset(token_request)
        output_text = result.get("output") if isinstance(result, dict) else result
        parsed = extract_json_object(output_text)
        if parsed:
            self._update_progress_from_worker_result(
                session_id=session_id,
                request_id=request_id,
                parsed=parsed,
            )
            return parsed

        output_str = str(output_text) if output_text is not None else ""
        output_lower = output_str.lower()
        if MAX_ITERATION_MESSAGE.lower() in output_lower:
            parsed = {
                "ok": False,
                "final": output_str,
                "page": {"url": "", "title": ""},
                "screenshot": None,
                "error": "max_iterations_reached",
                "status": "partial",
            }
            self._update_progress_from_worker_result(
                session_id=session_id,
                request_id=request_id,
                parsed=parsed,
            )
            return parsed

        parsed = {
            "ok": False,
            "final": output_str,
            "page": {"url": "", "title": ""},
            "screenshot": None,
            "error": "Browser worker did not return valid JSON output",
            "status": "failed",
        }
        self._update_progress_from_worker_result(
            session_id=session_id,
            request_id=request_id,
            parsed=parsed,
        )
        return parsed

    async def run_task_once(self, task: str, session_id: str, request_id: str) -> Dict[str, Any]:
        return await self._run_task_once(task=task, session_id=session_id, request_id=request_id)

    @staticmethod
    def _is_max_iteration_result(parsed: Dict[str, Any]) -> bool:
        if not isinstance(parsed, dict):
            return False
        if str(parsed.get("error", "")).strip().lower() == "max_iterations_reached":
            return True
        marker = MAX_ITERATION_MESSAGE.lower()
        for key in ("final", "error"):
            value = parsed.get(key)
            if value is None:
                continue
            if marker in str(value).lower():
                return True
        return False

    @staticmethod
    def _build_resume_task(task: str, previous_final: str, progress_context: str = "") -> str:
        base = (task or "").strip()
        previous = (previous_final or "").strip()
        if len(previous) > 1200:
            previous = previous[:1200] + "...[truncated]"
        context_parts: list[str] = [
            "Continuation context:",
            "- The previous run reached max iterations before completion.",
            "- Continue from the current browser state in this same session.",
            "- Avoid repeating already completed steps unless needed for recovery.",
        ]
        if progress_context:
            context_parts.append(progress_context)
        if previous:
            context_parts.extend(
                [
                    "- Previous partial status (may be incomplete):",
                    previous,
                ]
            )
        return f"{base}\n\n" + "\n".join(context_parts)

    @staticmethod
    def _trim_text(value: Any, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) > limit:
            return text[:limit] + "...[truncated]"
        return text

    @classmethod
    def _build_failure_summary(
        cls,
        *,
        task: str,
        error: str,
        page_url: str,
        page_title: str,
        final: str,
        screenshot: Any,
        attempt: int,
        progress_state: Optional[BrowserTaskProgressState] = None,
    ) -> str:
        lines = [
            "Failure summary for continuation:",
            f"- Original task: {cls._trim_text(task, 400) or '(empty)'}",
            f"- Failed attempt: {attempt}",
            f"- Error: {cls._trim_text(error, 300) or '(unknown)'}",
        ]
        if page_url or page_title:
            lines.append(
                f"- Last page: url={cls._trim_text(page_url, 240) or '(unknown)'}, "
                f"title={cls._trim_text(page_title, 120) or '(unknown)'}"
            )
        screenshot_text = cls._trim_text(screenshot, 200)
        if screenshot_text:
            lines.append(f"- Last screenshot: {screenshot_text}")
        progress_context = cls._build_progress_context(progress_state)
        if progress_context:
            lines.append(progress_context)
        final_excerpt = cls._trim_text(final, 1200)
        if final_excerpt:
            lines.append("- Partial output excerpt:")
            lines.append(final_excerpt)
        return "\n".join(lines)

    @staticmethod
    def _build_task_with_failure_context(task: str, failure_summary: str) -> str:
        base = (task or "").strip()
        summary = (failure_summary or "").strip()
        if not summary:
            return base
        return (
            f"{base}\n\n"
            "Previous failed attempt context:\n"
            f"{summary}\n\n"
            "Continuation instructions:\n"
            "- Continue from the current browser state in this same session.\n"
            "- Do not repeat completed steps unless required for recovery.\n"
            "- Prioritize resolving the listed failure."
        )

    async def run_task(
        self,
        task: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        await self.ensure_started()
        sid = self.session_new(session_id)
        rid = (request_id or "").strip() or uuid.uuid4().hex
        effective_timeout = int(timeout_s) if (timeout_s is not None and timeout_s > 0) else self.guardrails.timeout_s
        attempts = 2 if self.guardrails.retry_once else 1
        base_task = (task or "").strip()
        previous_failure_summary = self._failure_context_by_session.get(sid, "")

        async with self._locks[sid]:
            current_task = asyncio.current_task()
            if current_task is not None:
                self._register_inflight_task(sid, rid, current_task)
            try:
                if await self.is_cancelled(sid, rid):
                    await self.clear_cancel(sid, rid)
                    await self.clear_cancel(sid, None)
                    result = {
                        "ok": False,
                        "session_id": sid,
                        "request_id": rid,
                        "final": "",
                        "page": {"url": "", "title": ""},
                        "screenshot": None,
                        "error": "cancelled_by_frontend",
                        "attempt": 0,
                        "failure_summary": None,
                        "progress_state": None,
                    }
                    return result
                last_error: Optional[str] = None
                used_max_iteration_resume = False
                next_task = self._build_task_with_failure_context(base_task, previous_failure_summary)
                attempt_idx = 0
                max_attempts = attempts + (1 if self.guardrails.resume_on_max_iterations else 0)
                last_failure_final = ""
                last_failure_page: Dict[str, Any] = {}
                last_failure_screenshot: Any = None
                while attempt_idx < max_attempts:
                    try:
                        parsed = await asyncio.wait_for(
                            self.run_task_once(task=next_task, session_id=sid, request_id=rid),
                            timeout=float(effective_timeout),
                        )
                        attempt_idx += 1
                        self._update_progress_from_worker_result(
                            session_id=sid,
                            request_id=rid,
                            parsed=parsed,
                        )
                        parsed_ok = bool(parsed.get("ok", False))
                        if not parsed_ok and self._should_treat_as_completed(parsed):
                            parsed = dict(parsed)
                            parsed["ok"] = True
                            parsed["error"] = None
                            parsed_ok = True
                        should_resume_max_iter = (
                            (not parsed_ok)
                            and self._is_max_iteration_result(parsed)
                            and self.guardrails.resume_on_max_iterations
                            and not used_max_iteration_resume
                        )
                        if not parsed_ok:
                            last_error = str(parsed.get("error") or "")
                            last_failure_final = str(parsed.get("final", ""))
                            last_failure_page = parsed.get("page") if isinstance(parsed.get("page"), dict) else {}
                            last_failure_screenshot = parsed.get("screenshot")

                        if (
                            should_resume_max_iter
                        ):
                            used_max_iteration_resume = True
                            next_task = self._build_resume_task(
                                next_task,
                                str(parsed.get("final", "")),
                                progress_context=self._build_progress_context(
                                    self._progress_by_session.get(sid)
                                ),
                            )
                            last_error = str(parsed.get("error") or MAX_ITERATION_MESSAGE)
                            continue

                        if (
                            not parsed_ok
                            and attempt_idx < attempts
                            and self._is_retryable_runtime_result(parsed)
                        ):
                            page = parsed.get("page") if isinstance(parsed.get("page"), dict) else {}
                            failure_summary = self._build_failure_summary(
                                task=base_task,
                                error=str(parsed.get("error") or ""),
                                page_url=str(page.get("url", "")),
                                page_title=str(page.get("title", "")),
                                final=str(parsed.get("final", "")),
                                screenshot=parsed.get("screenshot"),
                                attempt=attempt_idx,
                                progress_state=self._progress_by_session.get(sid),
                            )
                            should_restart = (
                                self._is_retryable_transport_message(failure_summary)
                                or self._should_restart_after_runtime_result(parsed)
                            )
                            if should_restart:
                                try:
                                    await self._restart()
                                except Exception as restart_exc:
                                    last_error = f"restart_failed: {restart_exc!r}"
                                    break
                            next_task = self._build_task_with_failure_context(base_task, failure_summary)
                            continue

                        page = parsed.get("page") if isinstance(parsed.get("page"), dict) else {}
                        screenshot = self._normalize_screenshot_value(parsed.get("screenshot"))
                        response = {
                            "ok": parsed_ok,
                            "session_id": sid,
                            "request_id": rid,
                            "final": str(parsed.get("final", "")),
                            "page": {
                                "url": str(page.get("url", "")),
                                "title": str(page.get("title", "")),
                            },
                            "screenshot": screenshot,
                            "error": parsed.get("error"),
                            "attempt": attempt_idx,
                            "progress_state": None,
                        }
                        if parsed_ok:
                            self._failure_context_by_session.pop(sid, None)
                            self._progress_by_session.pop(sid, None)
                            response["failure_summary"] = None
                            return response

                        failure_summary = self._build_failure_summary(
                            task=base_task,
                            error=str(parsed.get("error") or ""),
                            page_url=str(page.get("url", "")),
                            page_title=str(page.get("title", "")),
                            final=str(parsed.get("final", "")),
                            screenshot=parsed.get("screenshot"),
                            attempt=attempt_idx,
                            progress_state=self._progress_by_session.get(sid),
                        )
                        self._failure_context_by_session[sid] = failure_summary
                        response["failure_summary"] = failure_summary
                        response["progress_state"] = self._export_progress_state(sid)
                        return response
                    except TimeoutError:
                        attempt_idx += 1
                        last_error = f"task_timeout: exceeded {effective_timeout}s"
                        if attempt_idx >= attempts:
                            break
                    except asyncio.CancelledError:
                        await self.clear_cancel(sid, rid)
                        await self.clear_cancel(sid, None)
                        result = {
                            "ok": False,
                            "session_id": sid,
                            "request_id": rid,
                            "final": "",
                            "page": {"url": "", "title": ""},
                            "screenshot": None,
                            "error": "cancelled_by_frontend",
                            "attempt": attempt_idx + 1,
                            "failure_summary": None,
                            "progress_state": None,
                        }
                        return result
                    except Exception as exc:
                        attempt_idx += 1
                        last_error = str(exc) or repr(exc)
                        if attempt_idx >= attempts:
                            break
                        # Restart before retry on known transport/session failures.
                        if (not str(exc)) or self._is_retryable_transport_error(exc):
                            try:
                                await self._restart()
                            except Exception as restart_exc:
                                last_error = f"restart_failed: {restart_exc!r}"
                                break

                await self.clear_cancel(sid, rid)
                await self.clear_cancel(sid, None)
                page_url = str(last_failure_page.get("url", "")) if isinstance(last_failure_page, dict) else ""
                page_title = str(last_failure_page.get("title", "")) if isinstance(last_failure_page, dict) else ""
                failure_summary = self._build_failure_summary(
                    task=base_task,
                    error=last_error or "unknown browser execution error",
                    page_url=page_url,
                    page_title=page_title,
                    final=last_failure_final,
                    screenshot=last_failure_screenshot,
                    attempt=min(attempt_idx, max_attempts),
                    progress_state=self._progress_by_session.get(sid),
                )
                self._failure_context_by_session[sid] = failure_summary
                result = {
                    "ok": False,
                    "session_id": sid,
                    "request_id": rid,
                    "final": "",
                    "page": {"url": "", "title": ""},
                    "screenshot": None,
                    "error": last_error or "unknown browser execution error",
                    "attempt": min(attempt_idx, max_attempts),
                    "failure_summary": failure_summary,
                    "progress_state": self._export_progress_state(sid),
                }
                return result
            finally:
                if current_task is not None:
                    self._unregister_inflight_task(sid, rid, current_task)

    async def shutdown(self) -> None:
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        try:
            if self.started:
                await Runner.stop()
            self.started = False
        finally:
            await self._stop_managed_driver()
