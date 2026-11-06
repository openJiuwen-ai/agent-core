# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package install, reload, and uninstall keep MCP declarations in step."""

import json
from pathlib import Path

import pytest

from openjiuwen.core.events.ops_impl.mcp.models import McpConnectOutput, McpStatus
from openjiuwen.core.events.ops_impl.mcp.runtime import set_resource_mgr_provider
from openjiuwen.core.events.types import Event, SessionContext
from openjiuwen.core.extension.schema.mcp import declarations
from openjiuwen.core.runner.resources_manager.base import Ok
from tests.unit_tests.core.extension.conftest import BUSINESS_EVENT, build_registry, write_package


class _RemovingResourceMgr:
    def __init__(self) -> None:
        self.removed: list[str] = []

    async def remove_mcp_server(self, server_id=None, **kwargs):
        self.removed.append(str(server_id))
        return []


@pytest.fixture(autouse=True)
def _reset_state():
    declarations.clear()
    previous = set_resource_mgr_provider(lambda: None)
    yield
    declarations.clear()
    set_resource_mgr_provider(previous)


def _write_mcp(root: Path, package_id: str, command: str) -> Path:
    write_package(root, package_id)
    (root / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "audit": {
                        "client_type": "stdio",
                        "params": {"command": command, "args": []},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_install_records_declarations(tmp_path: Path) -> None:
    source = _write_mcp(tmp_path / "source", "demo", "audit-mcp")
    registry, _ = await build_registry(tmp_path / "store")

    reports = await registry.install(source)

    assert reports[0].status == "SUCCESS"
    spec = declarations.resolve("demo", "audit")
    assert spec is not None
    assert spec.server_id == "mcp_demo_2a9751_audit"
    assert spec.params["command"] == "audit-mcp"


@pytest.mark.asyncio
async def test_install_rejects_invalid_mcp_json(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_package(source, "demo")
    (source / "mcp.json").write_text('{"mcpServers": {"Audit": {}}}', encoding="utf-8")
    registry, _ = await build_registry(tmp_path / "store")

    reports = await registry.install(source)

    assert reports[0].status == "FAILED"
    assert declarations.resolve("demo", "Audit") is None


@pytest.mark.asyncio
async def test_uninstall_removes_declarations_and_connection(tmp_path: Path) -> None:
    source = _write_mcp(tmp_path / "source", "demo", "audit-mcp")
    registry, _ = await build_registry(tmp_path / "store")
    await registry.install(source)
    manager = _RemovingResourceMgr()
    set_resource_mgr_provider(lambda: manager)

    report = await registry.uninstall("demo")

    assert report.status == "SUCCESS"
    assert declarations.resolve("demo", "audit") is None
    assert manager.removed == ["mcp_demo_2a9751_audit"]


@pytest.mark.asyncio
async def test_reload_keeps_unchanged_connection(tmp_path: Path) -> None:
    source = _write_mcp(tmp_path / "source", "demo", "audit-mcp")
    registry, _ = await build_registry(tmp_path / "store")
    await registry.install(source)
    manager = _RemovingResourceMgr()
    set_resource_mgr_provider(lambda: manager)

    report = await registry.reload("demo")

    assert report.status == "SUCCESS"
    assert manager.removed == []
    assert declarations.resolve("demo", "audit") is not None


@pytest.mark.asyncio
async def test_reload_replaces_changed_connection(tmp_path: Path) -> None:
    source = _write_mcp(tmp_path / "source", "demo", "audit-mcp")
    registry, _ = await build_registry(tmp_path / "store")
    await registry.install(source)
    manager = _RemovingResourceMgr()
    set_resource_mgr_provider(lambda: manager)
    _write_mcp((tmp_path / "store" / "demo" / "1.0.0"), "demo", "audit-mcp@2")

    report = await registry.reload("demo")

    assert report.status == "SUCCESS"
    assert manager.removed == ["mcp_demo_2a9751_audit"]
    spec = declarations.resolve("demo", "audit")
    assert spec is not None and spec.params["command"] == "audit-mcp@2"


_PLUGIN_BODY = f"""
from openjiuwen.core.events.ops_impl.mcp.models import McpConnectInput


def create_extension():
    return Extension()


class Extension:
    def register(self, registrar):
        async def handler(event, ops, next_handler):
            return await ops.mcp.connect(McpConnectInput(server_name="audit"))

        registrar.register_event_handler("{BUSINESS_EVENT}", handler, {{"tool": "Bash"}}, "user")
"""


class _OpeningResourceMgr:
    def __init__(self) -> None:
        self.configs: dict[str, object] = {}
        self.added: list[tuple[object, object, float | None]] = []

    def get_mcp_server_config(self, server_id: str):
        return self.configs.get(server_id)

    def get_mcp_tool_ids(self, server_id: str):
        return []

    async def get_tool_infos(self, tool_id=None, **kwargs):
        return []

    async def add_mcp_server(self, config, *, tag=None, timeout=None, **kwargs):  # noqa: ASYNC109
        self.added.append((config, tag, timeout))
        self.configs[config.server_id] = config
        return Ok(config.server_id)


@pytest.mark.asyncio
async def test_plugin_connect_opens_its_own_declaration(tmp_path: Path) -> None:
    source = _write_mcp(tmp_path / "source", "demo", "audit-mcp")
    write_package(source, "demo", body=_PLUGIN_BODY)
    registry, bus = await build_registry(tmp_path / "store")
    await registry.install(source)
    manager = _OpeningResourceMgr()
    set_resource_mgr_provider(lambda: manager)

    result = await bus.trigger(
        Event(BUSINESS_EVENT, {"tool": "Bash"}),
        SessionContext("session-demo"),
    )

    assert isinstance(result, McpConnectOutput)
    assert result.status is McpStatus.OK
    assert result.server_id == "mcp_demo_2a9751_audit"
    assert result.connected is True
    assert manager.added[0][1] == "demo"
    assert manager.added[0][2] == 60.0


class _FailingResourceMgr:
    async def remove_mcp_server(self, server_id=None, **kwargs):
        raise RuntimeError("cannot disconnect")


@pytest.mark.asyncio
async def test_uninstall_keeps_declarations_when_release_fails(tmp_path: Path) -> None:
    source = _write_mcp(tmp_path / "source", "demo", "audit-mcp")
    registry, _ = await build_registry(tmp_path / "store")
    await registry.install(source)
    set_resource_mgr_provider(lambda: _FailingResourceMgr())

    report = await registry.uninstall("demo")

    assert report.status == "FAILED"
    assert declarations.resolve("demo", "audit") is not None
