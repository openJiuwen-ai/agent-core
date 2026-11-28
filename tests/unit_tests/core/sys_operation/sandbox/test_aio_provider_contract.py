# coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AIO sandbox provider contract tests (mocked SDK client, no network).

Covers three contract gaps fixed in the aio provider:
  - upload_file/upload_file_stream honor overwrite=False: an existing remote
    target is refused, and a probe that cannot decide refuses the upload
    (fail-closed, matching jiuwenbox/adx).
  - execute_code forwards the caller's cwd instead of hard-coding "."
    (default stays "." when no cwd is given).
  - _read_file_bytes does not truncate content when chunk_size is given
    (chunk_size is a transfer hint, not a read limit).
"""

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.extensions.sys_operation.sandbox.providers.aio import AIOFSProvider


def _file_result(content: Any = "x", size: int = 1):
    return SimpleNamespace(data=SimpleNamespace(content=content, file_size=size))


def _find_result(files: list[str]):
    return SimpleNamespace(data=SimpleNamespace(files=files))


def _make_fs_provider(file_client: Any) -> AIOFSProvider:
    """Build an AIOFSProvider around a mocked agent_sandbox file client."""
    provider = object.__new__(AIOFSProvider)
    provider._client = SimpleNamespace(file=file_client)
    provider._timeout_seconds = 5
    provider._shell_provider = None
    # BaseProvider attributes used by _get_client paths
    provider.endpoint = SimpleNamespace(base_url="http://mock")
    return provider


class TestUploadFileOverwrite:
    @pytest.mark.asyncio
    async def test_refuses_existing_target_when_overwrite_false(self, tmp_path):
        uploads: list[dict] = []

        class _File:
            def find_files(self, *, path: str, glob: str):
                assert glob == "target.txt"
                return _find_result(["target.txt"])

            def upload_file(self, *, file, path: str):
                uploads.append({"path": path})
                return _file_result(size=3)

        provider = _make_fs_provider(_File())
        local = tmp_path / "source.txt"
        local.write_text("abc", encoding="utf-8")

        result = await provider.upload_file(str(local), "/dir/target.txt", overwrite=False)

        assert result.code != StatusCode.SUCCESS.code
        assert "already exists" in result.message
        assert uploads == [], "upload must not run when the target exists"

    @pytest.mark.asyncio
    async def test_uploads_when_target_missing(self, tmp_path):
        uploads: list[dict] = []

        class _File:
            def find_files(self, *, path: str, glob: str):
                return _find_result([])

            def upload_file(self, *, file, path: str):
                uploads.append({"path": path})
                return _file_result(size=3)

        provider = _make_fs_provider(_File())
        local = tmp_path / "source.txt"
        local.write_text("abc", encoding="utf-8")

        result = await provider.upload_file(str(local), "/dir/target.txt", overwrite=False)

        assert result.code == StatusCode.SUCCESS.code
        assert uploads == [{"path": "/dir/target.txt"}]

    @pytest.mark.asyncio
    async def test_overwrite_true_skips_existence_check(self, tmp_path):
        uploads: list[dict] = []
        checks: list[dict] = []

        class _File:
            def find_files(self, *, path: str, glob: str):
                checks.append({"path": path, "glob": glob})
                return _find_result(["target.txt"])

            def upload_file(self, *, file, path: str):
                uploads.append({"path": path})
                return _file_result(size=3)

        provider = _make_fs_provider(_File())
        local = tmp_path / "source.txt"
        local.write_text("abc", encoding="utf-8")

        result = await provider.upload_file(str(local), "/dir/target.txt", overwrite=True)

        assert result.code == StatusCode.SUCCESS.code
        assert uploads == [{"path": "/dir/target.txt"}]
        assert checks == [], "overwrite=True must not spend an existence round-trip"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_probe_failure_refuses_upload_fail_closed(self, tmp_path):
        """A failing existence probe must refuse the upload (fail-closed).

        jiuwenbox/adx turn probe failures into error results rather than
        overwriting an unverified target; the AIO provider must match that
        direction - a flaky check API must never degrade the overwrite
        protection into a silent clobber.
        """
        uploads: list[dict] = []

        class _File:
            def find_files(self, *, path: str, glob: str):
                raise RuntimeError("probe failed")

            def upload_file(self, *, file, path: str):
                uploads.append({"path": path})
                return _file_result(size=3)

        provider = _make_fs_provider(_File())
        local = tmp_path / "source.txt"
        local.write_text("abc", encoding="utf-8")

        result = await provider.upload_file(str(local), "/dir/target.txt", overwrite=False)

        assert result.code != StatusCode.SUCCESS.code
        assert "refusing to upload" in result.message
        assert uploads == [], "no upload may run when the probe cannot decide"

    @pytest.mark.asyncio
    async def test_object_shaped_probe_entries_are_handled(self, tmp_path):
        """Probe entries that are objects (not strings) must not crash silently.

        If find_files ever returns objects, an unreadable entry must surface
        as a refused upload (fail-closed) instead of an AttributeError
        swallowed into 'not exists'.
        """
        uploads: list[dict] = []

        class _File:
            def find_files(self, *, path: str, glob: str):
                return SimpleNamespace(data=SimpleNamespace(files=[object()]))

            def upload_file(self, *, file, path: str):
                uploads.append({"path": path})
                return _file_result(size=3)

        provider = _make_fs_provider(_File())
        local = tmp_path / "source.txt"
        local.write_text("abc", encoding="utf-8")

        result = await provider.upload_file(str(local), "/dir/target.txt", overwrite=False)

        assert result.code != StatusCode.SUCCESS.code
        assert uploads == []

    @pytest.mark.asyncio
    async def test_object_entries_with_path_attribute_match(self, tmp_path):
        """Object entries carrying a .path string are matched like strings."""
        uploads: list[dict] = []

        class _File:
            def find_files(self, *, path: str, glob: str):
                return SimpleNamespace(
                    data=SimpleNamespace(files=[SimpleNamespace(path="/dir/target.txt")])
                )

            def upload_file(self, *, file, path: str):
                uploads.append({"path": path})
                return _file_result(size=3)

        provider = _make_fs_provider(_File())
        local = tmp_path / "source.txt"
        local.write_text("abc", encoding="utf-8")

        result = await provider.upload_file(str(local), "/dir/target.txt", overwrite=False)

        assert result.code != StatusCode.SUCCESS.code
        assert "already exists" in result.message
        assert uploads == []


class TestUploadFileStreamOverwrite:
    @pytest.mark.asyncio
    async def test_refuses_existing_target_when_overwrite_false(self, tmp_path):
        writes: list[dict] = []

        class _File:
            def find_files(self, *, path: str, glob: str):
                return _find_result(["target.bin"])

            def write_file(self, *, file: str, content, append: bool):
                writes.append({"file": file})

        provider = _make_fs_provider(_File())
        local = tmp_path / "source.bin"
        local.write_bytes(b"\x00\x01\x02")

        chunks = [
            chunk
            async for chunk in provider.upload_file_stream(
                str(local), "/dir/target.bin", overwrite=False, chunk_size=2
            )
        ]

        assert all(c.code != StatusCode.SUCCESS.code for c in chunks)
        assert "already exists" in chunks[0].message
        assert writes == [], "no chunk may be written when the target exists"

    @pytest.mark.asyncio
    async def test_streams_when_target_missing(self, tmp_path):
        writes: list[dict] = []

        class _File:
            def find_files(self, *, path: str, glob: str):
                return _find_result([])

            def write_file(self, *, file: str, content, append: bool):
                writes.append({"file": file, "append": append})

        provider = _make_fs_provider(_File())
        local = tmp_path / "source.bin"
        local.write_bytes(b"\x00\x01\x02")

        chunks = [
            chunk
            async for chunk in provider.upload_file_stream(
                str(local), "/dir/target.bin", overwrite=False, chunk_size=2
            )
        ]

        assert chunks and all(c.code == StatusCode.SUCCESS.code for c in chunks)
        assert len(writes) == 2, "3 bytes with chunk_size=2 must stream as two writes"
        assert writes[0]["append"] is False and writes[1]["append"] is True


class TestReadFileBytesChunkSize:
    @pytest.mark.asyncio
    async def test_chunk_size_is_not_a_read_limit(self):
        class _File:
            def download_file(self, *, path: str):
                return [b"12345", b"6789", b"0"]

        provider = _make_fs_provider(_File())
        result = await provider._read_file_bytes("/data.bin", chunk_size=4)

        assert result.code == StatusCode.SUCCESS.code
        assert result.data.content == b"1234567890"

    @pytest.mark.asyncio
    async def test_default_chunk_size_unchanged(self):
        class _File:
            def download_file(self, *, path: str):
                return [b"12345", b"6789", b"0"]

        provider = _make_fs_provider(_File())
        result = await provider._read_file_bytes("/data.bin")

        assert result.code == StatusCode.SUCCESS.code
        assert result.data.content == b"1234567890"


class TestExecuteCodeCwdForwarding:
    """execute_code/execute_code_stream must forward the caller's cwd."""

    @staticmethod
    def _make_code_provider() -> "AIOCodeProvider":
        from openjiuwen.extensions.sys_operation.sandbox.providers.aio import AIOCodeProvider

        provider = object.__new__(AIOCodeProvider)
        provider._client = None
        provider._timeout_seconds = 5
        provider.endpoint = SimpleNamespace(base_url="http://mock")
        provider.config = None
        provider._shell_provider = None
        return provider

    @pytest.mark.asyncio
    async def test_execute_code_forwards_explicit_cwd(self, monkeypatch):
        observed: list[str | None] = []

        class _ShellProvider:
            async def execute_cmd(self, *, command, cwd, timeout, environment):
                observed.append(cwd)
                return SimpleNamespace(
                    code=StatusCode.SUCCESS.code,
                    message="Command executed successfully",
                    data=SimpleNamespace(stdout="", stderr="", exit_code=0),
                )

        provider = self._make_code_provider()
        provider._shell_provider = _ShellProvider()
        # _build_code_command is a static helper on the code provider; patch it
        # to a trivial CLI command so no temp file is involved.
        monkeypatch.setattr(
            type(provider), "_build_code_command",
            staticmethod(lambda code, language, force_file=False: ["python", "-c", code]),
        )

        result = await provider.execute_code("print('hi')", cwd="/data")

        assert observed == ["/data"], "caller cwd must be forwarded, not replaced"
        assert result.code == StatusCode.SUCCESS.code

    @pytest.mark.asyncio
    async def test_execute_code_defaults_cwd_to_dot(self, monkeypatch):
        observed: list[str | None] = []

        class _ShellProvider:
            async def execute_cmd(self, *, command, cwd, timeout, environment):
                observed.append(cwd)
                return SimpleNamespace(
                    code=StatusCode.SUCCESS.code,
                    message="Command executed successfully",
                    data=SimpleNamespace(stdout="", stderr="", exit_code=0),
                )

        provider = self._make_code_provider()
        provider._shell_provider = _ShellProvider()
        monkeypatch.setattr(
            type(provider), "_build_code_command",
            staticmethod(lambda code, language, force_file=False: ["python", "-c", code]),
        )

        result = await provider.execute_code("print('hi')")

        assert observed == ["."], "default remains '.' when no cwd is given"
        assert result.code == StatusCode.SUCCESS.code
