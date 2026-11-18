"""Public operation contracts preserved when adapting native ADX results."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.result import ExecuteCmdData, ExecuteCmdResult
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.extensions.sys_operation.sandbox.providers.adx_operations import (
    NativeCodeOperations,
    NativeFSOperations,
)


def _operation_provider(provider_type, **attributes):
    """Exercise result adapters through their backend-hook interface."""
    hooks = {name: lambda *_args, **_kwargs: None for name in provider_type.__abstractmethods__}
    concrete = type("OperationsUnderTest", (provider_type,), {**hooks, **attributes})
    return concrete(SandboxEndpoint(base_url="http://localhost:18080"))


@pytest.fixture
def fs_provider():
    provider = _operation_provider(NativeFSOperations)
    provider._fs_read_sync = MagicMock()
    provider._fs_write_sync = MagicMock()
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "selection", "expected"),
    [
        ("a\nb\nc", {}, "a\nb\nc"),
        ("a\nb\nc", {"head": 0}, "a\nb\nc"),
        ("a\nb\nc", {"head": 2}, "a\nb\n"),
        ("a\nb\nc", {"head": -1}, ""),
        ("a\nb\nc", {"tail": 0}, "a\nb\nc"),
        ("a\nb\nc", {"tail": 1}, "c"),
        ("a\nb\nc", {"tail": -1}, ""),
        ("a\nb\nc", {"line_range": (2, 99)}, "b\nc"),
        ("a\nb\nc", {"line_range": (4, 5)}, ""),
        ("a\nb\nc", {"line_range": (2, 1)}, ""),
        ("a\nb\nc", {"line_range": (0, 1)}, ""),
        ("", {"line_range": (1, 2)}, ""),
    ],
)
async def test_text_selection_and_stream_agree(fs_provider, content, selection, expected):
    provider = fs_provider
    provider._fs_read_sync.return_value = content

    result = await provider.read_file("/workspace/input", **selection)
    chunks = [chunk async for chunk in provider.read_file_stream("/workspace/input", **selection)]

    assert result.code == StatusCode.SUCCESS.code
    assert result.data.content == expected
    assert all(chunk.code == StatusCode.SUCCESS.code for chunk in chunks)
    assert "".join(chunk.data.chunk_content for chunk in chunks) == expected
    if chunks:
        assert [chunk.data.chunk_index for chunk in chunks] == list(range(len(chunks)))
        assert [chunk.data.is_last_chunk for chunk in chunks] == [False] * (len(chunks) - 1) + [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_size", [2, 0, -1])
async def test_binary_stream_preserves_payload_and_chunk_metadata(fs_provider, chunk_size):
    provider = fs_provider
    payload = b"\x00binary\xff"
    provider._fs_read_sync.return_value = payload

    chunks = [
        chunk async for chunk in provider.read_file_stream("/workspace/input", mode="bytes", chunk_size=chunk_size)
    ]

    assert b"".join(chunk.data.chunk_content for chunk in chunks) == payload
    assert all(chunk.data.mode == "bytes" for chunk in chunks)
    assert all(chunk.data.chunk_size == len(chunk.data.chunk_content) for chunk in chunks)
    assert [chunk.data.is_last_chunk for chunk in chunks] == [False] * (len(chunks) - 1) + [True]
    expected_chunks = (len(payload) + 1) // 2 if chunk_size == 2 else 1
    assert len(chunks) == expected_chunks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected"),
    [({}, "\npayload"), ({"prepend_newline": False}, "payload"), ({"append_newline": True}, "\npayload\n")],
)
async def test_write_preserves_newline_options(fs_provider, options, expected):
    provider = fs_provider

    result = await provider.write_file("/workspace/output", "payload", **options)

    assert result.code == StatusCode.SUCCESS.code
    assert result.data.size == len(expected)
    provider._fs_write_sync.assert_called_once_with("/workspace/output", expected, mode="w")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [("execution timeout", "execution timeout after 7 seconds"), ("failure reason: denied", "denied")],
)
async def test_code_failure_retains_reason_and_output(message, expected):
    provider = _operation_provider(NativeCodeOperations, shell_provider_type=MagicMock())
    provider._shell_provider.execute_cmd = AsyncMock(
        return_value=ExecuteCmdResult(
            code=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code,
            message=message,
            data=ExecuteCmdData(command="python", cwd="/tmp", stdout="partial", stderr="error", exit_code=-1),
        )
    )

    result = await provider.execute_code("print(1)", timeout=7)

    assert result.code == StatusCode.SYS_OPERATION_CODE_EXECUTION_ERROR.code
    assert expected in result.message
    assert result.data.stdout == "partial"
    assert result.data.stderr == "error"
    assert result.data.exit_code == -1
    provider._shell_provider.execute_cmd.assert_awaited_once()
