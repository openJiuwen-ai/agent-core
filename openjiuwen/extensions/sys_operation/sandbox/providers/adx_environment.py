# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""System operations backed by the public adx-environment SDK.

Environment identity is scoped by endpoint and isolation key. Workspace mounts
are independent of that identity and may be shared by multiple Agents.
Commands are submitted once with recoverable IDs; an unknown outcome is never
handled by recreating an environment or replaying user work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import threading
import uuid
from typing import Any, ClassVar
from urllib.parse import urlsplit

from openjiuwen.core.sys_operation.config import SandboxGatewayConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry
from openjiuwen.extensions.sys_operation.sandbox.providers.adx_operations import (
    NativeCodeOperations,
    NativeFSOperations,
    NativeShellOperations,
)

_PROCESS_IDENTITY = uuid.uuid4().hex


def _network_policy(configuration: dict[str, Any]) -> Any:
    """Rehydrate public SDK network types from serializable deployment config."""
    from adx_environment import NetworkPolicy, NetworkRule, PortRange, TrafficPolicy

    network = dict(configuration)
    if isinstance(network.get("traffic"), dict):
        traffic = dict(network["traffic"])
        rules = []
        for rule in traffic.get("rules", []):
            if isinstance(rule, dict):
                rule = dict(rule)
                for key in ("port_range", "environment_port_range"):
                    if isinstance(rule.get(key), dict):
                        rule[key] = PortRange(**rule[key])
                rule = NetworkRule(**rule)
            rules.append(rule)
        traffic["rules"] = rules
        network["traffic"] = TrafficPolicy(**traffic)
    return NetworkPolicy(**network)


def build_adx_shared_scope_key(base_url: str, isolation_key: str | None = None) -> str:
    """Build a stable provider scope without coupling it to the mounted path."""
    identity = json.dumps(
        [isolation_key or "system", os.environ.get("JIUWENSWARM_ADX_AGENT_KEY") or _PROCESS_IDENTITY],
        separators=(",", ":"),
    )
    return f"{base_url.rstrip('/')}|{identity}"


class _AdxEnvironmentBackend:
    endpoint: SandboxEndpoint
    _extra: dict[str, Any]
    _scope: str

    _lock: ClassVar[threading.RLock] = threading.RLock()
    # Shared by shell, code and fs; credentials remain in the SDK connection.
    _instances: ClassVar[dict[str, tuple[Any, Any]]] = {}
    _incarnations: ClassVar[dict[str, str]] = {}

    def _initialize_backend(self, endpoint: SandboxEndpoint, config: SandboxGatewayConfig | None) -> None:
        launcher = getattr(config, "launcher_config", None)
        self._extra = dict(getattr(launcher, "extra_params", None) or {})
        self._scope = build_adx_shared_scope_key(endpoint.base_url, endpoint.isolation_key)
        if (self._extra.get("user") is None) != (self._extra.get("group") is None):
            raise ValueError("ADX tool user and group must both be configured")
        if self._extra.get("user") is not None:
            for key in ("user", "group"):
                value = self._extra[key]
                if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
                    raise ValueError(f"ADX tool {key} must be a nonempty name or numeric ID")
                self._extra[key] = str(value).strip()
            identity = json.dumps([self._extra["user"], self._extra["group"]], separators=(",", ":"))
            self._scope += "|identity=" + hashlib.sha256(identity.encode()).hexdigest()

    def _get_environment(self) -> Any:
        from adx_environment import ConnectionConfig, Environment, EnvironmentNotFound, Mount  # pylint: disable=no-name-in-module

        with self._lock:
            cached = self._instances.get(self._scope)
            if cached is not None:
                try:
                    retired = cached[0].get_info().state in ("stopped", "deleted")
                except EnvironmentNotFound:
                    retired = True
                if not retired:
                    return cached[0]
                cached[0].close()
                del self._instances[self._scope]
                self._incarnations[self._scope] = uuid.uuid4().hex
            url = urlsplit(self.endpoint.base_url)
            if url.scheme not in {"https", "http"} or not url.netloc or url.path not in {"", "/"}:
                raise ValueError("ADX base_url must be an HTTP(S) origin")
            connection_args = dict(self._extra.get("connection") or {})
            connection_args.setdefault("server_address", url.netloc)
            connection_args.setdefault("use_tls", url.scheme == "https")
            connection_args.setdefault("token", os.environ.get("ADX_TOKEN", ""))
            connection_args.setdefault("verify_tls", True)
            connection = ConnectionConfig(**connection_args)
            kwargs = {
                key: self._extra[key]
                for key in (
                    "image",
                    "runtime",
                    "user",
                    "group",
                    "cpu",
                    "memory",
                    "cpu_limit",
                    "mem_limit",
                    "idle_timeout",
                    "env",
                    "create_timeout",
                    "schedule_timeout",
                )
                if key in self._extra
            }
            if not kwargs.get("image"):
                raise ValueError("ADX tool environment requires an image")
            kwargs.setdefault("runtime", "runsc")
            identity = self._scope + self._incarnations.get(self._scope, "")
            kwargs["name"] = "tools-" + hashlib.sha256(identity.encode()).hexdigest()[:40]
            kwargs["mounts"] = [Mount(**mount) for mount in self._extra.get("mounts", [])]
            if "network" in self._extra:
                kwargs["network"] = _network_policy(self._extra["network"])
            kwargs["cwd"] = self._extra.get("workdir")
            # Repeating this deterministic name must reconcile the same execution.
            # Unknown creates keep their name; never generate a fresh one on error.
            environment = Environment(connection=connection, **kwargs)
            self._instances[self._scope] = (environment, connection)
            return environment

    async def _run_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,  # noqa: ASYNC109 - SDK command timeout, not task cancellation
        environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._command_sync, command, cwd=cwd, timeout=timeout, envs=environment)

    def _command_sync(
        self, command: str, *, cwd: str | None = None, timeout: int | None = None, envs: dict[str, str] | None = None
    ) -> dict[str, Any]:
        from adx_environment.commands import CommandSubmissionError

        instance = self._get_environment()
        command_id = uuid.uuid4().hex
        try:
            handle = instance.commands.run(
                command, background=True, command_id=command_id, cwd=cwd, timeout=timeout, envs=envs
            )
        except CommandSubmissionError as error:
            raise RuntimeError(
                f"ADX command outcome unknown; command_id={error.command_id}; environment_id={error.environment_id}"
            ) from error
        result = handle.wait(timeout=timeout)
        exit_code = result.exit_code
        if exit_code is None:
            status = getattr(result.status, "value", result.status)
            if status == "TIMED_OUT":
                exit_code = 124
            else:
                error_code = getattr(result, "error_code", None) or str(status)
                error_message = getattr(result, "error_message", None) or "no exit code was returned"
                raise RuntimeError(
                    f"ADX command {error_code}: {error_message}; command_id={command_id}; "
                    f"environment_id={instance.id}. The command was not replayed."
                )
        stderr = result.stderr or ""
        if getattr(result, "truncated", False):
            streams = (
                ", ".join(name for name in ("stdout", "stderr") if getattr(result, f"{name}_truncated", False))
                or "output"
            )
            stderr += (
                f"\n[ADX {streams} truncated by the configured output limit; "
                f"command_id={command_id}; environment_id={instance.id}. "
                "For large results, write output to a file and read the file. The command was not replayed.]\n"
            )
        return {"returncode": exit_code, "stdout": result.stdout, "stderr": stderr}

    def _fs_read_sync(self, path: str, *, mode: str = "rb") -> str | bytes:
        return self._get_environment().files.read(path, format="bytes" if "b" in mode else "text")

    def _fs_write_sync(self, path: str, data: str | bytes, *, mode: str = "wb") -> None:
        instance = self._get_environment()
        if "a" not in mode:
            instance.files.write(path, data)
            return
        # Upload the payload separately so append never becomes a read/overwrite
        # race between Agents sharing a workspace, nor a shell argument-size limit.
        temporary = f"/tmp/.openjiuwen-append-{uuid.uuid4().hex}"
        instance.files.write(temporary, data)
        try:
            result = self._command_sync(f"cat -- {shlex.quote(temporary)} >> {shlex.quote(path)}", timeout=30)
            if result["returncode"] != 0:
                raise OSError(result["stderr"] or "ADX append failed")
        finally:
            instance.files.remove(temporary)

    def _fs_path_exists_sync(self, path: str) -> bool:
        return self._get_environment().files.exists(path)

    def _fs_list_sync(
        self,
        path: str,
        *,
        recursive: bool = False,
        max_depth: int | None = None,
        include_files: bool = True,
        include_dirs: bool = True,
    ) -> list[dict[str, Any]]:
        if max_depth is not None and max_depth < 0:
            raise ValueError("max_depth must be nonnegative")
        # AgentOS counts recursions below the root, including boundary entries.
        # ADX counts the root listing as depth one; its traversal ceiling is 64.
        depth = min(max_depth + 1, 64) if recursive and max_depth is not None else (64 if recursive else 1)
        try:
            items = self._get_environment().files.list(path, depth=depth)
        except FileNotFoundError as error:
            raise FileNotFoundError(f"path not found: {path}") from error
        except NotADirectoryError:
            return []
        result = []
        for entry in items:
            is_directory = entry.type == "dir"
            if (is_directory and not include_dirs) or (not is_directory and not include_files):
                continue
            result.append(
                {
                    "name": entry.name,
                    "path": entry.path,
                    "size": entry.size,
                    "modified_time": str(entry.modified_time),
                    "is_directory": is_directory,
                    "type": entry.type,
                }
            )
        return result

    def _fs_search_sync(
        self, path: str, pattern: str, exclude_patterns: list[str] | None = None
    ) -> list[dict[str, Any]]:
        items = self._get_environment().files.search(path, pattern, exclude_patterns=exclude_patterns)
        return [
            {
                "name": entry.name,
                "path": entry.path,
                "size": entry.size,
                "modified_time": str(entry.modified_time),
                "is_directory": entry.type == "dir",
                "type": entry.type,
            }
            for entry in items
        ]


async def delete_adx_environment(
    *, shared_key: str | None = None, base_url: str | None = None, reason: str = "teardown"
) -> list[str]:
    """Delete matching tool environments; retain failed entries for retry."""
    del reason
    from adx_environment import Environment  # pylint: disable=no-name-in-module

    def remove() -> list[str]:
        deleted = []
        with _AdxEnvironmentBackend._lock:
            keys = [
                key
                for key in _AdxEnvironmentBackend._instances
                if key == shared_key
                or (shared_key and key.startswith(shared_key + "|identity="))
                or (not shared_key and base_url and key.startswith(base_url.rstrip("/") + "|"))
            ]
            for key in keys:
                instance, connection = _AdxEnvironmentBackend._instances[key]
                Environment.delete(instance.id, connection=connection)
                instance.close()
                del _AdxEnvironmentBackend._instances[key]
                _AdxEnvironmentBackend._incarnations[key] = uuid.uuid4().hex
                deleted.append(key)
        return deleted

    return await asyncio.to_thread(remove)


@SandboxRegistry.provider("adx-environment", "fs")
class AdxEnvironmentFSProvider(_AdxEnvironmentBackend, NativeFSOperations):
    """Native ADX file operations."""


@SandboxRegistry.provider("adx-environment", "shell")
class AdxEnvironmentShellProvider(_AdxEnvironmentBackend, NativeShellOperations):
    """Native ADX command operations."""


@SandboxRegistry.provider("adx-environment", "code")
class AdxEnvironmentCodeProvider(_AdxEnvironmentBackend, NativeCodeOperations):
    """Code execution in the same environment as shell and file operations."""

    shell_provider_type = AdxEnvironmentShellProvider
