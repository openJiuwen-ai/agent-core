# coding: utf-8
from __future__ import annotations

import os
import uuid
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig


LONG_RUNNING_COMMAND = ["/usr/bin/python3", "-c", "import time; time.sleep(3600)"]
SANDBOX_BASE_PATH = "/tmp"


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint if "://" in endpoint else f"http://{endpoint}"


@pytest.fixture
def server_endpoint() -> str:
    return os.environ.get("JIUWENBOX_TEST_SERVER", "127.0.0.1:8321")


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(server_endpoint, monkeypatch) -> AsyncIterator[SysOperation]:
    base_url = _normalize_endpoint(server_endpoint)
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]

        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)
        await Runner.start()
        card_id = f"jiuwenbox_fs_op_{uuid.uuid4().hex[:8]}"
        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.CUSTOM,
                    custom_id=sandbox_id,
                ),
                launcher_config=PreDeployLauncherConfig(
                    base_url=base_url,
                    sandbox_type="jiuwenbox",
                    idle_ttl_seconds=600,
                ),
                timeout_seconds=30,
            ),
        )
        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()
        try:
            yield Runner.resource_mgr.get_sys_operation(card_id)
        finally:
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_read_write(sys_op):
    file_name = f"{SANDBOX_BASE_PATH}/test_basics_{uuid.uuid4().hex[:8]}.txt"
    content = "Hello, world!\nLine 2"

    write_res = await sys_op.fs().write_file(path=file_name, content=content, prepend_newline=False)
    assert write_res.code == StatusCode.SUCCESS.code

    read_res = await sys_op.fs().read_file(path=file_name)
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == content

    append_file = f"{SANDBOX_BASE_PATH}/test_append_{uuid.uuid4().hex[:8]}.txt"
    await sys_op.fs().write_file(path=append_file, content="Initial", prepend_newline=False)
    res = await sys_op.fs().read_file(path=append_file)
    assert res.data.content == "Initial"

    await sys_op.fs().write_file(
        path=append_file,
        content="Appended",
        mode="text",
        prepend_newline=True,
        append_newline=False,
    )
    res = await sys_op.fs().read_file(path=append_file)
    assert res.data.content == "\nAppended"

    bin_file = f"{SANDBOX_BASE_PATH}/test_{uuid.uuid4().hex[:8]}.bin"
    bin_data = b"\x00\x01\x02"
    await sys_op.fs().write_file(path=bin_file, content=bin_data, mode="bytes")
    read_bin = await sys_op.fs().read_file(path=bin_file, mode="bytes")
    assert read_bin.data.content == bin_data


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_read_head(sys_op):
    multi_line_file = f"{SANDBOX_BASE_PATH}/test_head_{uuid.uuid4().hex[:8]}.txt"
    multi_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    await sys_op.fs().write_file(multi_line_file, multi_content, prepend_newline=False)

    res = await sys_op.fs().read_file(path=multi_line_file, head=10)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == multi_content

    res = await sys_op.fs().read_file(path=multi_line_file, head=5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == multi_content

    res = await sys_op.fs().read_file(path=multi_line_file, head=3)
    assert res.code == StatusCode.SUCCESS.code
    expected_head = "Line 1\nLine 2\nLine 3\n"
    assert res.data.content == expected_head

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, head=3):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 3
    assert all(not is_last for _, is_last in chunks[:-1])
    assert chunks[-1][1] is True
    assert "".join(line for line, _ in chunks) == expected_head


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_read_tail(sys_op):
    multi_line_file = f"{SANDBOX_BASE_PATH}/test_tail_{uuid.uuid4().hex[:8]}.txt"
    multi_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    await sys_op.fs().write_file(multi_line_file, multi_content, prepend_newline=False)

    res = await sys_op.fs().read_file(path=multi_line_file, tail=10)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == multi_content

    res = await sys_op.fs().read_file(path=multi_line_file, tail=5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == multi_content

    res = await sys_op.fs().read_file(path=multi_line_file, tail=2)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 4\nLine 5"

    empty_file = f"{SANDBOX_BASE_PATH}/empty_{uuid.uuid4().hex[:8]}.txt"
    await sys_op.fs().write_file(empty_file, "", prepend_newline=False)
    res = await sys_op.fs().read_file(path=empty_file, tail=5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    single_line_file = f"{SANDBOX_BASE_PATH}/single_line_{uuid.uuid4().hex[:8]}.txt"
    await sys_op.fs().write_file(single_line_file, "Only one line", prepend_newline=False)
    res = await sys_op.fs().read_file(path=single_line_file, tail=5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Only one line"

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, tail=10):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 5
    assert all(not is_last for _, is_last in chunks[:-1])
    assert chunks[-1][1] is True
    assert "".join(line for line, _ in chunks) == multi_content

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, tail=2):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 2
    assert all(not is_last for _, is_last in chunks[:-1])
    assert chunks[-1][1] is True
    assert "".join(line for line, _ in chunks) == "Line 4\nLine 5"


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_read_line_range(sys_op):
    multi_line_file = f"{SANDBOX_BASE_PATH}/test_range_{uuid.uuid4().hex[:8]}.txt"
    multi_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    await sys_op.fs().write_file(multi_line_file, multi_content, prepend_newline=False)

    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(2, 4))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 2\nLine 3\nLine 4\n"

    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(1, 3))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 1\nLine 2\nLine 3\n"

    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(4, 5))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 4\nLine 5"

    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(4, 2))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(2, 4)):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 3
    assert all(not is_last for _, is_last in chunks[:-1])
    assert chunks[-1][1] is True
    assert "".join(line for line, _ in chunks) == "Line 2\nLine 3\nLine 4\n"

    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(2, 10))
    assert res.code == StatusCode.SUCCESS.code
    expected_range = "Line 2\nLine 3\nLine 4\nLine 5"
    assert res.data.content == expected_range

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(2, 10)):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 4
    assert chunks[-1][1] is True
    assert "".join(line for line, _ in chunks) == expected_range

    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(10, 20))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(10, 20)):
        chunks.append(chunk)
    assert len(chunks) == 0

    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(3, 3))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 3\n"

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(3, 3)):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 1
    assert chunks[0][1] is True
    assert chunks[0][0] == "Line 3\n"

    res = await sys_op.fs().read_file(path=multi_line_file, line_range=(5, 5))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 5"

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=multi_line_file, line_range=(5, 5)):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append((chunk.data.chunk_content, chunk.data.is_last_chunk))
    assert len(chunks) == 1
    assert chunks[0][1] is True
    assert chunks[0][0] == "Line 5"

    empty_file = f"{SANDBOX_BASE_PATH}/empty_range_{uuid.uuid4().hex[:8]}.txt"
    await sys_op.fs().write_file(empty_file, "", prepend_newline=False)
    res = await sys_op.fs().read_file(path=empty_file, line_range=(1, 5))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=empty_file, line_range=(1, 5)):
        chunks.append(chunk)
    assert len(chunks) == 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_read_file_mutually_exclusive_params(sys_op):
    test_file = f"{SANDBOX_BASE_PATH}/test_exclusive_{uuid.uuid4().hex[:8]}.txt"
    content = "line1\nline2\nline3\nline4\nline5"
    await sys_op.fs().write_file(test_file, content, prepend_newline=False)

    res = await sys_op.fs().read_file(path=test_file, head=2, tail=2)
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in res.message
    assert "head" in res.message
    assert "tail" in res.message

    res = await sys_op.fs().read_file(path=test_file, head=-1, line_range=(2, 4))
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in res.message
    assert "head" in res.message
    assert "line_range" in res.message

    res = await sys_op.fs().read_file(path=test_file, tail=2, line_range=(2, -1))
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in res.message
    assert "tail" in res.message
    assert "line_range" in res.message

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, head=-1, tail=2):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "cannot be specified simultaneously" in chunks[0].message

    res = await sys_op.fs().read_file(path=test_file, head=0, tail=2)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "line4\nline5"


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_read_file_negative_zero_params(sys_op):
    test_file = f"{SANDBOX_BASE_PATH}/test_neg_zero_{uuid.uuid4().hex[:8]}.txt"
    content = "line1\nline2\nline3\nline4\nline5"
    await sys_op.fs().write_file(test_file, content, prepend_newline=False)

    res = await sys_op.fs().read_file(path=test_file, head=-5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    res = await sys_op.fs().read_file(path=test_file, tail=-5)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    res = await sys_op.fs().read_file(path=test_file, head=0)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == content

    res = await sys_op.fs().read_file(path=test_file, tail=0)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == content

    res = await sys_op.fs().read_file(path=test_file, line_range=(0, 0))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    res = await sys_op.fs().read_file(path=test_file, line_range=(1, -1))
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == ""

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, head=-5):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SUCCESS.code
    assert chunks[0].data.chunk_content == ""

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, tail=-5):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SUCCESS.code
    assert chunks[0].data.chunk_content == ""

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, head=0):
        chunks.append(chunk)
    assert len(chunks) == 5
    assert chunks[0].code == StatusCode.SUCCESS.code
    assert "".join(chunk.data.chunk_content for chunk in chunks) == content


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_read_file_binary_mode_parameters(sys_op):
    test_file = f"{SANDBOX_BASE_PATH}/test_binary_{uuid.uuid4().hex[:8]}.txt"
    content = "Hello, world!\nLine 2"
    await sys_op.fs().write_file(test_file, content, prepend_newline=False)

    res = await sys_op.fs().read_file(path=test_file, mode="bytes", head=2)
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in res.message

    res = await sys_op.fs().read_file(path=test_file, mode="bytes", tail=2)
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in res.message

    res = await sys_op.fs().read_file(path=test_file, mode="bytes", line_range=(1, 2))
    assert res.code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in res.message

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, mode="bytes", head=2):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in chunks[0].message

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, mode="bytes", tail=2):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in chunks[0].message

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=test_file, mode="bytes", line_range=(1, 2)):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].code == StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR.code
    assert "only supported in text mode" in chunks[0].message


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_large_binary_file(sys_op):
    large_bin_file = f"{SANDBOX_BASE_PATH}/test_large_{uuid.uuid4().hex[:8]}.bin"
    large_bin_data = os.urandom(1024 * 1024)

    write_res = await sys_op.fs().write_file(path=large_bin_file, content=large_bin_data, mode="bytes")
    assert write_res.code == StatusCode.SUCCESS.code

    read_res = await sys_op.fs().read_file(path=large_bin_file, mode="bytes", chunk_size=0)
    assert read_res.code == StatusCode.SUCCESS.code
    assert read_res.data.content == large_bin_data

    chunks = []
    async for chunk in sys_op.fs().read_file_stream(path=large_bin_file, mode="bytes", chunk_size=1024 * 64):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append(chunk.data.chunk_content)

    streamed_data = b"".join(chunks)
    assert streamed_data == large_bin_data
    assert len(chunks) > 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_upload_download(sys_op, tmp_path):
    local_src = tmp_path / "upload_src.txt"
    local_src.write_text("Hello, upload and download!", encoding="utf-8")

    upload_target = f"{SANDBOX_BASE_PATH}/uploaded_{uuid.uuid4().hex[:8]}.txt"
    upload_res = await sys_op.fs().upload_file(local_path=str(local_src), target_path=upload_target)
    assert upload_res.code == StatusCode.SUCCESS.code

    download_target = tmp_path / "downloaded_file.txt"
    download_res = await sys_op.fs().download_file(source_path=upload_target, local_path=str(download_target))
    assert download_res.code == StatusCode.SUCCESS.code
    assert download_target.read_text(encoding="utf-8") == "Hello, upload and download!"

    stream_upload_target = f"{SANDBOX_BASE_PATH}/stream_uploaded_{uuid.uuid4().hex[:8]}.txt"
    chunks = []
    async for chunk in sys_op.fs().upload_file_stream(local_path=str(local_src), target_path=stream_upload_target):
        assert chunk.code == StatusCode.SUCCESS.code
        chunks.append(chunk)
    assert len(chunks) > 0

    stream_download_target = tmp_path / "stream_downloaded.txt"
    download_chunks = []
    async for chunk in sys_op.fs().download_file_stream(
        source_path=stream_upload_target,
        local_path=str(stream_download_target),
    ):
        assert chunk.code == StatusCode.SUCCESS.code
        download_chunks.append(chunk)
    assert len(download_chunks) > 0
    assert stream_download_target.read_text(encoding="utf-8") == "Hello, upload and download!"

    empty_local_src = tmp_path / "empty.txt"
    empty_local_src.write_text("", encoding="utf-8")
    empty_upload_target = f"{SANDBOX_BASE_PATH}/empty_uploaded_{uuid.uuid4().hex[:8]}.txt"
    empty_upload_res = await sys_op.fs().upload_file(local_path=str(empty_local_src), target_path=empty_upload_target)
    assert empty_upload_res.code == StatusCode.SUCCESS.code

    empty_download_target = tmp_path / "empty_downloaded.txt"
    empty_download_res = await sys_op.fs().download_file(
        source_path=empty_upload_target,
        local_path=str(empty_download_target),
    )
    assert empty_download_res.code == StatusCode.SUCCESS.code
    assert empty_download_target.read_text(encoding="utf-8") == ""

    large_local_src = tmp_path / "large_upload_test.txt"
    large_content = "x" * (1024 * 1024)
    large_local_src.write_text(large_content, encoding="utf-8")

    upload_target_0 = f"{SANDBOX_BASE_PATH}/uploaded_large_0_{uuid.uuid4().hex[:8]}.txt"
    upload_res_0 = await sys_op.fs().upload_file(
        local_path=str(large_local_src),
        target_path=upload_target_0,
        chunk_size=0,
    )
    assert upload_res_0.code == StatusCode.SUCCESS.code

    upload_target_neg1 = f"{SANDBOX_BASE_PATH}/uploaded_large_neg1_{uuid.uuid4().hex[:8]}.txt"
    upload_res_neg1 = await sys_op.fs().upload_file(
        local_path=str(large_local_src),
        target_path=upload_target_neg1,
        chunk_size=-1,
    )
    assert upload_res_neg1.code == StatusCode.SUCCESS.code

    download_target_0 = tmp_path / "downloaded_large_0.txt"
    download_res_0 = await sys_op.fs().download_file(
        source_path=upload_target_0,
        local_path=str(download_target_0),
        chunk_size=0,
    )
    assert download_res_0.code == StatusCode.SUCCESS.code

    download_target_neg1 = tmp_path / "downloaded_large_neg1.txt"
    download_res_neg1 = await sys_op.fs().download_file(
        source_path=upload_target_neg1,
        local_path=str(download_target_neg1),
        chunk_size=-1,
    )
    assert download_res_neg1.code == StatusCode.SUCCESS.code

    assert download_target_0.read_text(encoding="utf-8") == large_content
    assert download_target_neg1.read_text(encoding="utf-8") == large_content


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_list_operations(sys_op):
    test_dir = f"{SANDBOX_BASE_PATH}/test_list_{uuid.uuid4().hex[:8]}"
    await sys_op.fs().write_file(f"{test_dir}/file1.txt", "Content 1", prepend_newline=False)
    await sys_op.fs().write_file(f"{test_dir}/dir1/file2.txt", "Content 2", prepend_newline=False)
    await sys_op.fs().write_file(f"{test_dir}/dir1/subdir1/file3.txt", "Content 3", prepend_newline=False)
    await sys_op.fs().write_file(f"{test_dir}/dir2/file4.txt", "Content 4", prepend_newline=False)

    list_res = await sys_op.fs().list_files(test_dir)
    assert list_res.code == StatusCode.SUCCESS.code
    assert len(list_res.data.list_items) >= 1

    recursive_res = await sys_op.fs().list_files(test_dir, recursive=True)
    assert recursive_res.code == StatusCode.SUCCESS.code
    assert len(recursive_res.data.list_items) >= 4

    txt_files_res = await sys_op.fs().list_files(test_dir, recursive=True, file_types=[".txt"])
    assert txt_files_res.code == StatusCode.SUCCESS.code
    assert len(txt_files_res.data.list_items) >= 4

    dirs_res = await sys_op.fs().list_directories(test_dir)
    assert dirs_res.code == StatusCode.SUCCESS.code
    assert len(dirs_res.data.list_items) >= 2

    recursive_dirs_res = await sys_op.fs().list_directories(test_dir, recursive=True)
    assert recursive_dirs_res.code == StatusCode.SUCCESS.code
    assert len(recursive_dirs_res.data.list_items) >= 3

    empty_dir = f"{test_dir}/empty_dir"
    await sys_op.shell().execute_cmd(f"mkdir -p {empty_dir}")

    empty_list_res = await sys_op.fs().list_files(empty_dir)
    assert empty_list_res.code == StatusCode.SUCCESS.code
    assert len(empty_list_res.data.list_items) == 0

    empty_dirs_res = await sys_op.fs().list_directories(empty_dir)
    assert empty_dirs_res.code == StatusCode.SUCCESS.code
    assert len(empty_dirs_res.data.list_items) == 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_search_operations(sys_op):
    test_dir = f"{SANDBOX_BASE_PATH}/test_search_{uuid.uuid4().hex[:8]}"
    await sys_op.fs().write_file(f"{test_dir}/test1.txt", "Content 1", prepend_newline=False)
    await sys_op.fs().write_file(f"{test_dir}/test2.txt", "Content 2", prepend_newline=False)
    await sys_op.fs().write_file(f"{test_dir}/data1.csv", "CSV content", prepend_newline=False)
    await sys_op.fs().write_file(f"{test_dir}/data2.csv", "More CSV", prepend_newline=False)
    await sys_op.fs().write_file(f"{test_dir}/subdir/test3.txt", "Content 3", prepend_newline=False)

    txt_search_res = await sys_op.fs().search_files(test_dir, "*.txt")
    assert txt_search_res.code == StatusCode.SUCCESS.code
    assert len(txt_search_res.data.matching_files) >= 3

    csv_search_res = await sys_op.fs().search_files(test_dir, "*.csv")
    assert csv_search_res.code == StatusCode.SUCCESS.code
    assert len(csv_search_res.data.matching_files) >= 2

    exclude_search_res = await sys_op.fs().search_files(test_dir, "*", exclude_patterns=["*.csv"])
    assert exclude_search_res.code == StatusCode.SUCCESS.code
    csv_files = [item for item in exclude_search_res.data.matching_files if item.name.endswith(".csv")]
    assert len(csv_files) == 0

    no_match_res = await sys_op.fs().search_files(test_dir, "*.xyz")
    assert no_match_res.code == StatusCode.SUCCESS.code
    assert len(no_match_res.data.matching_files) == 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_write_file_append_text(sys_op):
    append_file = f"{SANDBOX_BASE_PATH}/test_append_text_{uuid.uuid4().hex[:8]}.txt"

    await sys_op.fs().write_file(path=append_file, content="Line 1", prepend_newline=False, append=False)
    res = await sys_op.fs().read_file(path=append_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 1"

    await sys_op.fs().write_file(path=append_file, content="Line 2", prepend_newline=False, append=True)
    res = await sys_op.fs().read_file(path=append_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 1Line 2"

    await sys_op.fs().write_file(path=append_file, content="\nLine 3", prepend_newline=False, append=True)
    res = await sys_op.fs().read_file(path=append_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "Line 1Line 2\nLine 3"


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_write_file_append_binary(sys_op):
    append_bin_file = f"{SANDBOX_BASE_PATH}/test_append_binary_{uuid.uuid4().hex[:8]}.bin"

    await sys_op.fs().write_file(path=append_bin_file, content=b"\x00\x01", mode="bytes", append=False)
    res = await sys_op.fs().read_file(path=append_bin_file, mode="bytes")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == b"\x00\x01"

    await sys_op.fs().write_file(path=append_bin_file, content=b"\x02\x03", mode="bytes", append=True)
    res = await sys_op.fs().read_file(path=append_bin_file, mode="bytes")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == b"\x00\x01\x02\x03"

    await sys_op.fs().write_file(path=append_bin_file, content=b"\x04\x05", mode="bytes", append=True)
    res = await sys_op.fs().read_file(path=append_bin_file, mode="bytes")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == b"\x00\x01\x02\x03\x04\x05"


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_fs_write_file_append_new_file(sys_op):
    new_file = f"{SANDBOX_BASE_PATH}/test_append_new_file_{uuid.uuid4().hex[:8]}.txt"

    res = await sys_op.fs().write_file(path=new_file, content="First content", prepend_newline=False, append=True)
    assert res.code == StatusCode.SUCCESS.code

    res = await sys_op.fs().read_file(path=new_file)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.content == "First content"
