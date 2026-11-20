# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package ``mcp.json`` parsing and the process-wide declaration table."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.extension.schema.mcp import (
    McpDeclarationTable,
    load_mcp_config,
    parse_mcp_config,
)

_DECLARATION = {
    "mcpServers": {
        "weather": {
            "client_type": "stdio",
            "params": {"command": "npx", "args": ["-y", "weather-mcp"]},
        }
    }
}


def _write(tmp_path: Path, payload: object) -> Path:
    (tmp_path / "mcp.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_missing_file_means_no_servers(tmp_path: Path) -> None:
    assert load_mcp_config(tmp_path, "audit.pkg") == ()


def test_parse_declares_one_server(tmp_path: Path) -> None:
    specs = load_mcp_config(_write(tmp_path, _DECLARATION), "audit.pkg")

    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "weather"
    assert spec.server_id == "mcp_audit_pkg_d4352b_weather"
    assert spec.server_name == "audit_pkg_d4352b__weather"
    assert spec.server_path == "stdio://weather"
    assert spec.client_type == "stdio"
    assert spec.params["command"] == "npx"
    assert spec.include_image_content is False


def test_parse_keeps_explicit_server_path(tmp_path: Path) -> None:
    payload = {
        "mcpServers": {
            "remote": {
                "client_type": "streamable-http",
                "server_path": "https://example.test/mcp",
                "auth_headers": {"Authorization": "Bearer t"},
            }
        }
    }

    spec = load_mcp_config(_write(tmp_path, payload), "pkg")[0]

    assert spec.server_path == "https://example.test/mcp"
    assert spec.auth_headers == {"Authorization": "Bearer t"}


def test_package_ids_that_normalize_alike_stay_distinct() -> None:
    dotted = parse_mcp_config(_DECLARATION, "audit.pkg")[0]
    underscored = parse_mcp_config(_DECLARATION, "audit_pkg")[0]

    assert dotted.server_id != underscored.server_id
    assert dotted.server_name != underscored.server_name


def test_digest_changes_with_configuration() -> None:
    first = parse_mcp_config(_DECLARATION, "pkg")[0]
    changed = json.loads(json.dumps(_DECLARATION))
    changed["mcpServers"]["weather"]["params"]["args"] = ["-y", "weather-mcp@2"]

    second = parse_mcp_config(changed, "pkg")[0]

    assert first.server_id == second.server_id
    assert first.digest != second.digest


@pytest.mark.parametrize(
    "payload",
    [
        {"servers": {}},
        {"mcpServers": {"Weather": {}}},
        {"mcpServers": {"weather": {"client_type": "stdio", "unknown": 1}}},
        {"mcpServers": {"weather": {"client_type": "stdio", "params": []}}},
        {"mcpServers": {"weather": {"client_type": "stdio", "params": {}}}},
        {"mcpServers": {"weather": {"client_type": "stdio", "params": {"command": " "}}}},
        {"mcpServers": {"weather": {"client_type": "stdio", "include_image_content": "yes"}}},
        {"mcpServers": {"weather": {"client_type": "http"}}},
    ],
)
def test_invalid_document_is_rejected(payload: object) -> None:
    with pytest.raises(ValidationError) as invalid:
        parse_mcp_config(payload, "pkg")

    assert invalid.value.status is StatusCode.EXTENSION_MANIFEST_INVALID


def test_table_replaces_and_withdraws_a_package() -> None:
    specs = parse_mcp_config(_DECLARATION, "pkg")
    table = McpDeclarationTable()

    previous = table.declare("pkg", specs)

    assert previous == ()
    assert table.resolve("pkg", "weather") == specs[0]
    assert table.resolve_server_id(specs[0].server_id) == specs[0]
    assert table.withdraw("pkg") == specs
    assert table.resolve("pkg", "weather") is None
