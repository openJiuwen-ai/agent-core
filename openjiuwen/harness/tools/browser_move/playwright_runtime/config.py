# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime configuration helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from openjiuwen.core.foundation.tool import McpServerConfig

from ..utils.env import (
    DEFAULT_BROWSER_TIMEOUT_S,
    DEFAULT_GUARDRAIL_MAX_FAILURES,
    DEFAULT_GUARDRAIL_MAX_STEPS,
    DEFAULT_GUARDRAIL_RETRY_ONCE,
    DEFAULT_MODEL_NAME,  # noqa: F401  (re-exported for main.py / mcp_server / tests)
    DEFAULT_PLAYWRIGHT_MCP_ARGS,
    DEFAULT_PLAYWRIGHT_MCP_COMMAND,
    MISSING_API_KEY_MESSAGE,  # noqa: F401  (re-exported for main.py / mcp_server)
    first_non_empty_env,
    is_truthy_env,
    load_repo_dotenv,  # noqa: F401  (re-exported for main.py / mcp_server)
    parse_command_args,
    resolve_bool_env,
    resolve_browser_timeout_s,
    resolve_int_env,
    resolve_model_name,
    resolve_model_settings,
)


@dataclass
class BrowserRunGuardrails:
    max_steps: int = 20
    max_failures: int = 2
    timeout_s: int = 180
    retry_once: bool = True
    resume_on_max_iterations: bool = False


@dataclass(frozen=True)
class BrowserInstanceConfig:
    """Per-instance browser identity, used to isolate one browser per agent.

    All fields default to empty/0, which reproduces the legacy process-global
    (env-driven) behavior. When ``key`` is non-empty the browser is isolated:
    the Playwright MCP ``server_id`` is suffixed with the key and the managed
    profile / port / user-data-dir are derived from it instead of shared env.
    Agents sharing the same ``key`` intentionally share one browser.
    """

    key: str = ""
    driver_mode: str = ""  # "", "managed", "remote", "extension"; "" -> env/default
    managed_port: int = 0  # 0 -> auto-allocate (keyed) or env/default (legacy)
    user_data_dir: str = ""  # "" -> derived from key under mcp_cwd/.browser-profiles
    profile_name: str = ""  # "" -> key, then env BROWSER_PROFILE_NAME
    cdp_url: str = ""  # remote mode: explicit CDP endpoint
    browser_binary: str = ""  # optional Chrome path override

    def sanitized_key(self) -> str:
        """Return the key reduced to id-safe characters (``[A-Za-z0-9_-]``)."""
        return re.sub(r"[^A-Za-z0-9_-]+", "-", (self.key or "").strip()).strip("-")


@dataclass(frozen=True)
class RuntimeSettings:
    provider: str
    api_key: str
    api_base: str
    model_name: str
    mcp_cfg: McpServerConfig
    guardrails: BrowserRunGuardrails
    instance: Optional[BrowserInstanceConfig] = None


def resolve_playwright_mcp_cwd() -> str:
    """Resolve MCP working directory with relocatable defaults."""
    configured = (
        (os.getenv("PLAYWRIGHT_RUNTIME_MCP_CWD") or "").strip()
        or (os.getenv("BROWSER_RUNTIME_MCP_CWD") or "").strip()
        or (os.getenv("PLAYWRIGHT_RUNTIME_WORKDIR") or "").strip()
        or (os.getenv("BROWSER_RUNTIME_WORKDIR") or "").strip()
    )
    if configured:
        return str(Path(configured).expanduser().resolve())
    return str(Path.cwd().expanduser().resolve())


def build_browser_guardrails() -> BrowserRunGuardrails:
    return BrowserRunGuardrails(
        max_steps=resolve_int_env(
            "BROWSER_GUARDRAIL_MAX_STEPS",
            default=DEFAULT_GUARDRAIL_MAX_STEPS,
            minimum=1,
        ),
        max_failures=resolve_int_env(
            "BROWSER_GUARDRAIL_MAX_FAILURES",
            default=DEFAULT_GUARDRAIL_MAX_FAILURES,
            minimum=0,
        ),
        timeout_s=resolve_browser_timeout_s(),
        retry_once=resolve_bool_env(
            "BROWSER_GUARDRAIL_RETRY_ONCE",
            default=DEFAULT_GUARDRAIL_RETRY_ONCE,
        ),
        resume_on_max_iterations=resolve_bool_env(
            "BROWSER_GUARDRAIL_RESUME_ON_MAX_ITERATIONS",
            default=False,
        ),
    )


def build_playwright_mcp_config(instance: Optional[BrowserInstanceConfig] = None) -> McpServerConfig:
    command = (
        os.getenv("PLAYWRIGHT_MCP_COMMAND", DEFAULT_PLAYWRIGHT_MCP_COMMAND).strip() or DEFAULT_PLAYWRIGHT_MCP_COMMAND
    )
    args = parse_command_args(os.getenv("PLAYWRIGHT_MCP_ARGS", DEFAULT_PLAYWRIGHT_MCP_ARGS))
    cwd = resolve_playwright_mcp_cwd()
    driver_mode = (os.getenv("BROWSER_DRIVER") or "").strip().lower()
    extension_mode = driver_mode == "extension" or is_truthy_env(os.getenv("PLAYWRIGHT_MCP_EXTENSION") or "")
    timeout_s = resolve_int_env(
        "PLAYWRIGHT_MCP_TIMEOUT_S",
        "BROWSER_TIMEOUT_S",
        default=DEFAULT_BROWSER_TIMEOUT_S,
        minimum=1,
    )

    env_map: Dict[str, str] = {}
    for key in (
        "PLAYWRIGHT_BROWSERS_PATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ):
        value = os.getenv(key)
        if value:
            env_map[key] = value

    extra_env_json = (os.getenv("PLAYWRIGHT_MCP_ENV_JSON") or "").strip()
    if extra_env_json:
        try:
            extra = json.loads(extra_env_json)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    env_map[str(k)] = str(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid PLAYWRIGHT_MCP_ENV_JSON: {exc}") from exc

    if extension_mode:
        env_map["PLAYWRIGHT_MCP_EXTENSION"] = "true"
        extension_token = first_non_empty_env("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
        if extension_token:
            env_map["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = extension_token
        if "--extension" not in args:
            args.append("--extension")
    else:
        # CDP support for official Playwright MCP server. An explicit per-instance
        # cdp_url (remote mode) wins over the shared env endpoint.
        instance_cdp = instance.cdp_url.strip() if instance and instance.cdp_url else ""
        cdp_endpoint = instance_cdp or first_non_empty_env("PLAYWRIGHT_MCP_CDP_ENDPOINT", "PLAYWRIGHT_CDP_URL")
        cdp_headers = first_non_empty_env("PLAYWRIGHT_MCP_CDP_HEADERS", "PLAYWRIGHT_CDP_HEADERS")
        cdp_timeout = first_non_empty_env("PLAYWRIGHT_MCP_CDP_TIMEOUT", "PLAYWRIGHT_CDP_TIMEOUT_MS")
        browser_name = first_non_empty_env("PLAYWRIGHT_MCP_BROWSER")
        device_name = first_non_empty_env("PLAYWRIGHT_MCP_DEVICE")

        if cdp_endpoint:
            if device_name:
                raise ValueError("PLAYWRIGHT_MCP_DEVICE is not supported with CDP endpoint mode.")
            env_map["PLAYWRIGHT_MCP_CDP_ENDPOINT"] = cdp_endpoint
            if not browser_name:
                # CDP mode is Chromium-only.
                env_map["PLAYWRIGHT_MCP_BROWSER"] = "chrome"
        if cdp_headers:
            env_map["PLAYWRIGHT_MCP_CDP_HEADERS"] = cdp_headers
        if cdp_timeout:
            env_map["PLAYWRIGHT_MCP_CDP_TIMEOUT"] = cdp_timeout

    params: Dict[str, Any] = {
        "command": command,
        "args": args,
        "cwd": cwd,
    }
    if timeout_s > 0:
        params["timeout_s"] = timeout_s
    if env_map:
        params["env"] = env_map

    # Isolate the MCP registration per browser identity. Same key -> same
    # server_id (intentional sharing); different key -> isolated server.
    server_id = "playwright_official_stdio"
    server_name = "playwright-official"
    instance_key = instance.sanitized_key() if instance else ""
    if instance_key:
        server_id = f"{server_id}__{instance_key}"
        server_name = f"{server_name}-{instance_key}"

    return McpServerConfig(
        server_id=server_id,
        server_name=server_name,
        server_path="stdio://playwright",
        client_type="stdio",
        params=params,
    )


def build_runtime_settings(instance: Optional[BrowserInstanceConfig] = None) -> RuntimeSettings:
    provider, api_key, api_base = resolve_model_settings()
    return RuntimeSettings(
        provider=provider,
        api_key=api_key,
        api_base=api_base,
        model_name=resolve_model_name(),
        mcp_cfg=build_playwright_mcp_config(instance),
        guardrails=build_browser_guardrails(),
        instance=instance,
    )
