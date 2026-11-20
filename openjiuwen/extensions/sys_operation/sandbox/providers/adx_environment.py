# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""System operations backed by the public adx-environment SDK.

Environment identity is scoped by endpoint and isolation key. Workspace mounts
are independent of that identity and may be shared by multiple Agents.
Commands are submitted once with recoverable IDs; an unknown outcome is never
handled by recreating an environment or replaying user work.

Serializable ``rootfs={"type": "local", "path": "/node/rootfs"}`` settings use
the public LocalRootfs SDK type. Image conversion and environment extraction
are owned by the caller; the existing provider cache remains shared by operations.

A launcher may configure ``endpoint_file`` as a read-only node context JSON
with ``ingress_url`` and optional ``node_id``. It is read before every environment
lookup; malformed or missing configured context is an error. Node changes select
a fresh SDK connection scope even when bridge URLs are identical. Serialized
traffic and DNS policies are rehydrated through the public SDK types.

Idle lifecycle settings are passed through to the SDK create call:
``idle_action`` (``delete`` or ``pause``), ``pause_policy`` (``retention``,
``timeoutSeconds`` and ``storage``) and ``failover``. Non-default lifecycle
settings participate in the environment scope, so changing them selects a new
environment instead of silently keeping an environment created with the old
settings. A ``paused`` environment stays cached: the first data-plane operation
wakes it and keeps the retained checkpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import threading
import uuid
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import SplitResult, urlsplit

from openjiuwen.core.sys_operation.config import SandboxGatewayConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry
from openjiuwen.extensions.sys_operation.sandbox.providers.adx_operations import (
    NativeCodeOperations,
    NativeFSOperations,
    NativeShellOperations,
)

ADX_ENVIRONMENT_CAPABILITIES = frozenset({"local-rootfs"})

_PROCESS_IDENTITY = uuid.uuid4().hex
_DEFAULT_RESTART_POLICY = {"maxAttempts": 3, "initialBackoffSeconds": 1, "maxBackoffSeconds": 30}


def _restart_policy(value: Any) -> Any:
    """Convert serializable tool settings into the public SDK restart policy."""
    from adx_environment import RestartPolicy

    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(_DEFAULT_RESTART_POLICY):
        raise ValueError("ADX tool restart_policy requires maxAttempts, initialBackoffSeconds and maxBackoffSeconds")
    return RestartPolicy(
        max_attempts=value["maxAttempts"],
        initial_backoff_seconds=value["initialBackoffSeconds"],
        max_backoff_seconds=value["maxBackoffSeconds"],
    )


_IDLE_ACTIONS = ("delete", "pause")
_PAUSE_STORAGE = ("configured", "local", "shared")


def _idle_action(value: Any) -> Any:
    """Convert a serializable idle action into the public SDK enum."""
    from adx_environment import IdleAction

    if not isinstance(value, str) or value not in _IDLE_ACTIONS:
        raise ValueError("ADX tool idle_action must be one of: " + ", ".join(_IDLE_ACTIONS))
    return IdleAction(value)


def _pause_policy(value: Any) -> Any:
    """Convert serializable pause settings into the public SDK policy types."""
    from adx_environment import CheckpointStorage, PausePolicy, RetentionPolicy

    if not isinstance(value, dict):
        raise ValueError("ADX tool pause_policy must be a mapping")
    unknown = sorted(set(value) - {"retention", "timeoutSeconds", "storage"})
    if unknown:
        raise ValueError("ADX tool pause_policy has unsupported fields: " + ", ".join(unknown))
    arguments: dict[str, Any] = {}
    retention = value.get("retention")
    if retention is not None:
        if not isinstance(retention, dict) or set(retention) - {"kind", "ttlSeconds"}:
            raise ValueError("ADX tool pause_policy.retention must contain kind and optional ttlSeconds")
        kind = retention.get("kind")
        ttl = retention.get("ttlSeconds")
        if kind == "forever" and "ttlSeconds" not in retention:
            arguments["retention"] = RetentionPolicy.forever()
        elif kind == "ttl" and isinstance(ttl, int) and not isinstance(ttl, bool):
            if ttl <= 0:
                raise ValueError("ADX tool pause_policy.retention.ttlSeconds must be a positive integer")
            arguments["retention"] = RetentionPolicy.for_seconds(ttl)
        else:
            raise ValueError("ADX tool pause_policy.retention requires kind 'forever' or 'ttl' with ttlSeconds")
    storage = value.get("storage")
    if storage is not None:
        if storage not in _PAUSE_STORAGE:
            raise ValueError("ADX tool pause_policy.storage must be one of: " + ", ".join(_PAUSE_STORAGE))
        arguments["storage"] = CheckpointStorage(storage)
    if "timeoutSeconds" in value:
        timeout = value["timeoutSeconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("ADX tool pause_policy.timeoutSeconds must be a positive integer")
        arguments["timeout_seconds"] = timeout
    return PausePolicy(**arguments)


def _failover(value: Any) -> bool:
    """Validate the same-node recovery flag before handing it to the SDK."""
    if not isinstance(value, bool):
        raise ValueError("ADX tool failover must be a boolean")
    return value


def _lifecycle_signature(settings: dict[str, Any]) -> str:
    """Separate cached environments when create-time lifecycle settings change."""
    lifecycle: dict[str, Any] = {}
    if settings.get("idle_action") not in (None, "delete"):
        lifecycle["idle_action"] = settings["idle_action"]
        if settings.get("idle_timeout") is not None:
            lifecycle["idle_timeout"] = settings["idle_timeout"]
        if settings.get("pause_policy") is not None:
            lifecycle["pause_policy"] = settings["pause_policy"]
    if settings.get("failover") not in (None, False):
        lifecycle["failover"] = settings["failover"]
    if not lifecycle:
        return ""
    payload = json.dumps(lifecycle, sort_keys=True, separators=(",", ":"))
    return "|lifecycle=" + hashlib.sha256(payload.encode()).hexdigest()


def _network_policy(configuration: dict[str, Any]) -> Any:
    """Rehydrate public SDK network types from serializable deployment config."""
    from adx_environment import DNSPolicy, DNSRule, NetworkPolicy, NetworkRule, PortRange, TrafficPolicy

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
    if isinstance(network.get("dns"), dict):
        dns = dict(network["dns"])
        dns["rules"] = [DNSRule(**rule) if isinstance(rule, dict) else rule for rule in dns.get("rules", [])]
        network["dns"] = DNSPolicy(**dns)
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
    _scope_suffix: str

    _lock: ClassVar[threading.RLock] = threading.RLock()
    # Shared by shell, code and fs; credentials remain in the SDK connection.
    _instances: ClassVar[dict[str, tuple[Any, Any]]] = {}
    _incarnations: ClassVar[dict[str, str]] = {}

    def _initialize_backend(self, endpoint: SandboxEndpoint, config: SandboxGatewayConfig | None) -> None:
        launcher = getattr(config, "launcher_config", None)
        self._extra = dict(getattr(launcher, "extra_params", None) or {})
        self._scope_suffix = ""
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
            self._scope_suffix = "|identity=" + hashlib.sha256(identity.encode()).hexdigest()
        if "rootfs" in self._extra:
            rootfs = self._extra["rootfs"]
            if not isinstance(rootfs, dict) or rootfs.get("type") != "local":
                raise ValueError("ADX tool rootfs requires type=local and a node path")
            path = rootfs.get("path")
            if not isinstance(path, str) or not path.startswith("/"):
                raise ValueError("ADX tool rootfs requires an absolute node path")
            if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in path) or any(
                part in (".", "..") for part in path.split("/")
            ):
                raise ValueError("ADX tool rootfs cannot contain traversal or control characters")
            artifact = json.dumps({"type": "local", "path": path}, sort_keys=True)
            self._scope_suffix += "|rootfs=" + hashlib.sha256(artifact.encode()).hexdigest()
        lifecycle = _lifecycle_signature(self._extra)
        if lifecycle:
            self._scope_suffix += lifecycle

    def _resolve_endpoint(self) -> tuple[SplitResult, str]:
        base_url = self.endpoint.base_url
        node_scope = ""
        endpoint_file = self._extra.get("endpoint_file")
        if endpoint_file:
            node = json.loads(Path(endpoint_file).read_text(encoding="utf-8"))
            if not isinstance(node, dict):
                raise ValueError("ADX endpoint_file must contain a JSON object")
            base_url = node.get("ingress_url")
            if node.get("node_id"):
                if not isinstance(node["node_id"], str):
                    raise ValueError("ADX endpoint_file node_id must be a string")
                node_scope = "|node=" + hashlib.sha256(node["node_id"].encode()).hexdigest()
            if not isinstance(base_url, str) or not base_url:
                raise ValueError("ADX endpoint_file requires an ingress_url")
        url = urlsplit(base_url)
        if url.scheme not in {"https", "http"} or not url.netloc or url.path not in {"", "/"}:
            raise ValueError("ADX base_url must be an HTTP(S) origin")
        if url.username is not None or url.password is not None:
            raise ValueError("ADX base_url must be an HTTP(S) origin")
        if url.query or url.fragment:
            raise ValueError("ADX base_url must be an HTTP(S) origin")
        # Keep teardown under the configured scope while separating resolved node endpoints.
        scope = (
            build_adx_shared_scope_key(self.endpoint.base_url, self.endpoint.isolation_key)
            + self._scope_suffix
            + node_scope
        )
        if base_url.rstrip("/") != self.endpoint.base_url.rstrip("/"):
            scope += "|endpoint=" + hashlib.sha256(base_url.rstrip("/").encode()).hexdigest()
        return url, scope

    @staticmethod
    def _cached_environment_retired(instance: Any, restart: Any) -> bool:
        state = instance.get_info().state
        if state == "failed" and restart is not None:
            # The SDK summary does not expose restart_pending. ADX owns
            # recovery and its budget; replacing here could race backoff
            # or reset exhausted attempts. Never replay unknown work.
            raise RuntimeError(
                f"ADX tool Environment {instance.id} is failed; ADX owns automatic restart; "
                "retry after recovery or inspect the Environment after retry exhaustion"
            )
        if state == "paused":
            # The retained checkpoint belongs to this execution. Keep the
            # cached Environment so the first data-plane operation wakes it.
            return False
        return state in ("stopped", "deleted", "failed")

    def _get_environment(self) -> Any:
        import adx_environment
        from adx_environment import (  # pylint: disable=no-name-in-module
            ConnectionConfig,
            Environment,
            EnvironmentNotFound,
            IdleAction,
            Mount,
        )

        if "rootfs" in self._extra and "local-rootfs" not in getattr(adx_environment, "SDK_CAPABILITIES", ()):
            raise ValueError("Local rootfs requires adx-environment 0.1.0rc11 or newer")

        with self._lock:
            url, self._scope = self._resolve_endpoint()
            kwargs = {"restart_policy": _restart_policy(self._extra.get("restart_policy", _DEFAULT_RESTART_POLICY))}
            # Validate create-time lifecycle settings even when a cached environment
            # is reused; a changed configuration must never pass silently.
            lifecycle_kwargs: dict[str, Any] = {}
            if "idle_action" in self._extra:
                lifecycle_kwargs["idle_action"] = _idle_action(self._extra["idle_action"])
            if "pause_policy" in self._extra:
                lifecycle_kwargs["pause_policy"] = _pause_policy(self._extra["pause_policy"])
            if "failover" in self._extra:
                lifecycle_kwargs["failover"] = _failover(self._extra["failover"])
            if lifecycle_kwargs.get("idle_action") == IdleAction.PAUSE:
                if self._extra.get("runtime", "runsc") == "conch":
                    raise ValueError("ADX tool idle_action=pause is not supported by the conch runtime")
                if "idle_timeout" in self._extra:
                    idle_timeout = self._extra["idle_timeout"]
                    if isinstance(idle_timeout, bool) or not isinstance(idle_timeout, int) or idle_timeout <= 0:
                        raise ValueError("ADX tool idle_action=pause requires a positive idle_timeout")
            cached = self._instances.get(self._scope)
            if cached is not None:
                try:
                    # Idle deletion remains replaceable; never replay unknown work.
                    retired = self._cached_environment_retired(cached[0], kwargs["restart_policy"])
                except EnvironmentNotFound:
                    retired = True
                if not retired:
                    return cached[0]
                cached[0].close()
                del self._instances[self._scope]
                self._incarnations[self._scope] = uuid.uuid4().hex
            connection_args = dict(self._extra.get("connection") or {})
            if self._extra.get("endpoint_file"):
                for key in ("gateway_address", "gateway_use_tls"):
                    connection_args.pop(key, None)
                connection_args.update(
                    server_address=url.netloc, use_tls=url.scheme == "https", verify_tls=url.scheme == "https"
                )
            connection_args.setdefault("server_address", url.netloc)
            connection_args.setdefault("use_tls", url.scheme == "https")
            connection_args.setdefault("token", os.environ.get("ADX_TOKEN", ""))
            connection_args.setdefault("verify_tls", True)
            connection = ConnectionConfig(**connection_args)
            for key in (
                "image",
                "rootfs",
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
            ):
                if key in self._extra:
                    kwargs[key] = self._extra[key]
            if kwargs.get("rootfs"):
                kwargs["rootfs"] = adx_environment.LocalRootfs(path=self._extra["rootfs"]["path"])
                kwargs.pop("image", None)
            if not kwargs.get("image"):
                kwargs.pop("image", None)
            kwargs.setdefault("runtime", "runsc")
            kwargs.update(lifecycle_kwargs)
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

    @staticmethod
    def _matches_scope(key: str, shared_key: str | None, base_url: str | None) -> bool:
        if key == shared_key:
            return True
        if shared_key:
            return key.startswith(
                (
                    shared_key + "|identity=",
                    shared_key + "|node=",
                    shared_key + "|rootfs=",
                    shared_key + "|endpoint=",
                    shared_key + "|lifecycle=",
                )
            )
        if base_url:
            return key.startswith(base_url.rstrip("/") + "|")
        return False

    @classmethod
    def delete_environments(cls, shared_key: str | None, base_url: str | None) -> list[str]:
        """Delete matching cached environments, retaining failed entries for retry."""
        from adx_environment import Environment  # pylint: disable=no-name-in-module

        deleted = []
        failed = []
        with cls._lock:
            keys = []
            for key in cls._instances:
                if cls._matches_scope(key, shared_key, base_url):
                    keys.append(key)
            for key in keys:
                instance, connection = cls._instances[key]
                try:
                    Environment.delete(instance.id, connection=connection)
                    instance.close()
                except Exception as error:  # pylint: disable=broad-exception-caught
                    # Retain this entry for retry, but finish the other independent cleanups.
                    failed.append((instance.id, error))
                    continue
                del cls._instances[key]
                cls._incarnations[key] = uuid.uuid4().hex
                deleted.append(key)
        if failed:
            details = "; ".join(f"{environment_id}: {error}" for environment_id, error in failed)
            raise RuntimeError(f"ADX tool cleanup failed: {details}") from failed[0][1]
        return deleted

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
        return self._command_result(result, command_id, instance.id)

    @staticmethod
    def _command_result(result: Any, command_id: str, environment_id: str) -> dict[str, Any]:
        """Preserve command status and truncation details without replaying work."""
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
                    f"environment_id={environment_id}. The command was not replayed."
                )
        stderr = result.stderr or ""
        if getattr(result, "truncated", False):
            streams = (
                ", ".join(name for name in ("stdout", "stderr") if getattr(result, f"{name}_truncated", False))
                or "output"
            )
            stderr += (
                f"\n[ADX {streams} truncated by the configured output limit; "
                f"command_id={command_id}; environment_id={environment_id}. "
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
            include_entry = include_dirs if is_directory else include_files
            if not include_entry:
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
    return await asyncio.to_thread(_AdxEnvironmentBackend.delete_environments, shared_key, base_url)


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
