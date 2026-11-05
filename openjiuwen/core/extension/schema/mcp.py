# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MCP servers a package declares in ``mcp.json``.

A declaration is metadata only: loading a package records it in the
process-wide table below and never opens a connection. The first
``mcp.connect`` event resolves the declaration and registers the server with
the resource manager, which is the only point where a connection is created.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.errors import ExtensionFailure

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ALLOWED_FIELDS = frozenset(
    {
        "client_type",
        "server_path",
        "params",
        "auth_headers",
        "auth_query_params",
        "include_image_content",
    }
)
_MAX_SAFE_PART = 40
_PACKAGE_DIGEST_LEN = 6
_DEFAULT_CLIENT_TYPE = "sse"


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    """One MCP server declared by a package, named relative to that package."""

    package_id: str
    name: str
    server_id: str
    server_name: str
    client_type: str
    server_path: str
    params: dict[str, Any]
    auth_headers: dict[str, str]
    auth_query_params: dict[str, str]
    include_image_content: bool
    digest: str

    def config_kwargs(self) -> dict[str, Any]:
        """Return the arguments ``McpServerConfig`` needs for this declaration."""
        return {
            "server_id": self.server_id,
            "server_name": self.server_name,
            "server_path": self.server_path,
            "client_type": self.client_type,
            "params": dict(self.params),
            "auth_headers": dict(self.auth_headers),
            "auth_query_params": dict(self.auth_query_params),
            "include_image_content": self.include_image_content,
        }


def mcp_server_name(package_id: str, name: str) -> str:
    """Return the process-wide server name of one package declaration."""
    return f"{_package_part(package_id)}__{name}"


def mcp_server_id(package_id: str, name: str) -> str:
    """Return the stable resource id of one package declaration."""
    return f"mcp_{_package_part(package_id)}_{name}"


def load_mcp_config(package_root: Path, package_id: str) -> tuple[McpServerSpec, ...]:
    """Read ``package_root / mcp.json`` for one package.

    A missing file means the package declares no MCP server. A file that
    exists must be valid, so a broken declaration fails the package install
    instead of being silently ignored.

    Raises:
        ExtensionFailure: The file is unreadable or violates the declaration
            rules.
    """
    root = package_root.expanduser().resolve()
    path = root / "mcp.json"
    if not path.is_file():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _invalid(f"cannot read mcp.json: {exc}", cause=exc) from exc
    return parse_mcp_config(raw, package_id)


def parse_mcp_config(raw: object, package_id: str) -> tuple[McpServerSpec, ...]:
    """Build declarations from an already loaded ``mcp.json`` document.

    Raises:
        ExtensionFailure: The document is not a mapping, carries unknown
            fields, or one server declaration is invalid.
    """
    if not isinstance(raw, dict):
        raise _invalid("mcp.json top level must be a mapping")
    unknown = set(raw) - {"mcpServers"}
    if unknown:
        raise _invalid(f"mcp.json has unknown top-level field(s): {', '.join(sorted(unknown))}")
    servers = raw.get("mcpServers")
    if servers is None:
        return ()
    if not isinstance(servers, dict):
        raise _invalid("mcpServers must be a mapping")
    return tuple(_parse_server(package_id, name, entry) for name, entry in servers.items())


def _parse_server(package_id: str, name: object, entry: object) -> McpServerSpec:
    if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
        raise _invalid(f"mcpServers key is invalid: {name!r}")
    if not isinstance(entry, dict):
        raise _invalid(f"mcpServers.{name} must be a mapping")
    unknown = set(entry) - _ALLOWED_FIELDS
    if unknown:
        raise _invalid(f"mcpServers.{name} has unknown field(s): {', '.join(sorted(unknown))}")

    client_type = entry.get("client_type", _DEFAULT_CLIENT_TYPE)
    if not isinstance(client_type, str) or not client_type.strip():
        raise _invalid(f"mcpServers.{name}.client_type must be a non-empty string")
    client_type = client_type.strip()

    server_path = entry.get("server_path")
    if server_path is None and client_type == "stdio":
        server_path = f"stdio://{name}"
    if not isinstance(server_path, str) or not server_path.strip():
        raise _invalid(f"mcpServers.{name}.server_path must be a non-empty string")

    params = _mapping(entry.get("params"), f"mcpServers.{name}.params")
    if client_type == "stdio":
        command = params.get("command")
        if not isinstance(command, str) or not command.strip():
            raise _invalid(f"mcpServers.{name}.params.command must be a non-empty string")
    auth_headers = _string_mapping(entry.get("auth_headers"), f"mcpServers.{name}.auth_headers")
    auth_query_params = _string_mapping(entry.get("auth_query_params"), f"mcpServers.{name}.auth_query_params")
    include_image_content = entry.get("include_image_content", False)
    if not isinstance(include_image_content, bool):
        raise _invalid(f"mcpServers.{name}.include_image_content must be a boolean")

    return McpServerSpec(
        package_id=package_id,
        name=name,
        server_id=mcp_server_id(package_id, name),
        server_name=mcp_server_name(package_id, name),
        client_type=client_type,
        server_path=server_path.strip(),
        params=params,
        auth_headers=auth_headers,
        auth_query_params=auth_query_params,
        include_image_content=include_image_content,
        digest=_digest(
            {
                "client_type": client_type,
                "server_path": server_path.strip(),
                "params": params,
                "auth_headers": auth_headers,
                "auth_query_params": auth_query_params,
                "include_image_content": include_image_content,
            }
        ),
    )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _invalid(f"{field} must be a mapping with string keys")
    return dict(value)


def _string_mapping(value: object, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _invalid(f"{field} must be a mapping with string keys")
    if any(not isinstance(item, str) for item in value.values()):
        raise _invalid(f"{field} values must be strings")
    return dict(value)


def _digest(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _package_part(package_id: str) -> str:
    """Return a readable package prefix that stays unique across package ids."""

    safe = _safe_part(package_id, "pkg")
    digest = hashlib.sha256(package_id.encode("utf-8")).hexdigest()[:_PACKAGE_DIGEST_LEN]
    return f"{safe}_{digest}"


def _safe_part(value: str, default: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return (normalized or default)[:_MAX_SAFE_PART]


def _invalid(reason: str, *, cause: BaseException | None = None) -> ExtensionFailure:
    return ExtensionFailure(StatusCode.EXTENSION_MANIFEST_INVALID, reason=reason, cause=cause)


class McpDeclarationTable:
    """Process-wide map from a package to the MCP servers it declares.

    The table describes installed packages, so it is keyed by ``package_id``
    rather than by EventBus. Reload replaces one package's entries and
    uninstall removes them.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_package: dict[str, dict[str, McpServerSpec]] = {}

    def declare(
        self,
        package_id: str,
        specs: tuple[McpServerSpec, ...],
    ) -> tuple[McpServerSpec, ...]:
        """Replace one package's declarations and return the previous ones."""
        with self._lock:
            previous = self._by_package.get(package_id, {})
            self._by_package[package_id] = {spec.name: spec for spec in specs}
            return tuple(previous.values())

    def withdraw(self, package_id: str) -> tuple[McpServerSpec, ...]:
        """Drop one package's declarations and return what was removed."""
        with self._lock:
            previous = self._by_package.pop(package_id, {})
        return tuple(previous.values())

    def specs_for(self, package_id: str) -> tuple[McpServerSpec, ...]:
        """Return one package's declarations in declaration order."""
        with self._lock:
            return tuple(self._by_package.get(package_id, {}).values())

    def resolve(self, package_id: str, name: str) -> McpServerSpec | None:
        """Return one declaration by package-local name."""
        with self._lock:
            return self._by_package.get(package_id, {}).get(name)

    def resolve_server_id(self, server_id: str) -> McpServerSpec | None:
        """Return one declaration by its process-wide server id."""
        with self._lock:
            for specs in self._by_package.values():
                for spec in specs.values():
                    if spec.server_id == server_id:
                        return spec
        return None

    def clear(self) -> None:
        """Drop every declaration. Tests and process shutdown use this."""
        with self._lock:
            self._by_package.clear()


declarations = McpDeclarationTable()

__all__ = [
    "McpDeclarationTable",
    "McpServerSpec",
    "declarations",
    "load_mcp_config",
    "mcp_server_id",
    "mcp_server_name",
    "parse_mcp_config",
]
