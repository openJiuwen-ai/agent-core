# coding: utf-8
"""Unit tests for workspace directory creation decisions."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.harness.workspace.directory_builder import DirectoryBuilder


def _make_builder():
    sys_operation = MagicMock()
    sys_operation.fs.return_value.write_file = AsyncMock()
    return DirectoryBuilder(sys_operation=sys_operation, root_path="workspace")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node",
    [
        {"name": "unix", "path": "/external/logs"},
        {"name": "windows", "path": r"C:\\external\\logs"},
        {"name": "marked", "path": "managed", "external": True},
    ],
)
async def test_skips_external_or_absolute_nodes(node):
    builder = _make_builder()

    with patch("openjiuwen.harness.workspace.directory_builder.logger.info") as info:
        await builder.build([node])

    builder.sys_operation.fs.return_value.write_file.assert_not_awaited()
    info.assert_called_once()


@pytest.mark.asyncio
async def test_creates_relative_nodes():
    builder = _make_builder()

    await builder.build([{"name": "managed", "path": "managed"}])

    builder.sys_operation.fs.return_value.write_file.assert_awaited_once_with(
        "workspace/managed/.workspace",
        content="",
        create_if_not_exist=True,
    )


@pytest.mark.asyncio
async def test_rejects_unsafe_relative_nodes():
    builder = _make_builder()

    with pytest.raises(ValueError, match="Unsafe path detected"):
        await builder.build([{"name": "unsafe", "path": "../outside"}])

    with pytest.raises(ValueError, match="Unsafe path detected"):
        await builder.build([{"name": "drive-relative", "path": "C:outside"}])

    builder.sys_operation.fs.return_value.write_file.assert_not_awaited()
