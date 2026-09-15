# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for jiuwenbox extra.paths client routing (no live box-server)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as jb


def _ok_response(json_data=None, content=b"{}", status=200, method="GET", url="http://t/x"):
    resp = MagicMock()
    resp.is_success = status < 400
    resp.status_code = status
    resp.reason_phrase = "OK"
    resp.content = content if isinstance(content, (bytes, bytearray)) else b"{}"
    resp.json.return_value = json_data if json_data is not None else {}
    req = MagicMock()
    req.method = method
    req.url = url
    resp.request = req
    return resp


def _client(platform: str | None = "linux") -> jb._JiuwenBoxClient:
    obj = jb._JiuwenBoxClient.__new__(jb._JiuwenBoxClient)
    obj._platform = platform
    obj._client = MagicMock()
    return obj


class TestNormalizeAccessExtra:
    def test_copies_options_paths_only(self):
        extra = jb._JiuwenBoxClient._normalize_access_extra(
            {"extra": {"paths": ["C:\\ws", ""]}, "sandbox_path": "C:\\ws\\a.txt"}
        )
        assert extra == {"paths": ["C:\\ws"]}

    def test_never_fills_from_target_path(self):
        extra = jb._JiuwenBoxClient._normalize_access_extra(
            {"path": "C:\\secret\\file.txt"}
        )
        assert extra == {"paths": []}

    def test_missing_options_is_empty(self):
        assert jb._JiuwenBoxClient._normalize_access_extra(None) == {"paths": []}


class TestMixinFrozenExtra:
    def test_options_win_over_launcher_snapshot(self):
        mixin = jb._JiuwenBoxProviderMixin.__new__(jb._JiuwenBoxProviderMixin)
        mixin.config = SimpleNamespace(
            launcher_config=SimpleNamespace(
                extra_params={"access_extra_paths": ["C:\\auto"]},
            ),
        )
        payload = mixin._access_extra_payload({"extra": {"paths": ["D:\\approved"]}})
        assert payload["paths"][0] == "C:\\auto"
        assert "D:\\approved" in payload["paths"]

    def test_falls_back_to_launcher_snapshot(self):
        mixin = jb._JiuwenBoxProviderMixin.__new__(jb._JiuwenBoxProviderMixin)
        mixin.config = SimpleNamespace(
            launcher_config=SimpleNamespace(
                extra_params={"access_extra_paths": ["C:\\auto"]},
            ),
        )
        payload = mixin._access_extra_payload({})
        assert payload == {"paths": ["C:\\auto"]}

    def test_options_positional_plus_kwargs_does_not_collide(self):
        mixin = jb._JiuwenBoxProviderMixin.__new__(jb._JiuwenBoxProviderMixin)
        mixin.config = SimpleNamespace(
            launcher_config=SimpleNamespace(extra_params={}),
        )
        kwargs = {
            "options": {"extra": {"paths": ["D:\\approved"]}},
            "encoding": "utf-8",
        }
        payload = mixin._access_extra_payload(kwargs.get("options"), **kwargs)
        assert payload["paths"] == ["D:\\approved"]

    def test_contextvar_unions_with_frozen_snapshot(self):
        from openjiuwen.harness.security.permission_engine.access_extra import (
            bind_tool_access_extra,
            reset_tool_access_extra,
        )

        mixin = jb._JiuwenBoxProviderMixin.__new__(jb._JiuwenBoxProviderMixin)
        mixin.config = SimpleNamespace(
            launcher_config=SimpleNamespace(
                extra_params={"access_extra_paths": ["C:\\auto"]},
            ),
        )
        token = bind_tool_access_extra({"extra": {"paths": ["E:\\llm"]}})
        try:
            payload = mixin._access_extra_payload({})
        finally:
            reset_tool_access_extra(token)
        assert payload["paths"][0] == "C:\\auto"
        assert "E:\\llm" in payload["paths"]


class TestHealthPlatform:
    def test_missing_platform_fails_closed(self):
        client = _client(platform=None)
        client._client.get.return_value = _ok_response({"windows_supported": True})
        with pytest.raises(RuntimeError, match="health.platform"):
            client.server_platform()

    def test_caches_windows_and_linux(self):
        client = _client(platform=None)
        client._client.get.return_value = _ok_response({"platform": "windows"})
        assert client.server_platform() == "windows"
        assert client._is_windows_server() is True
        client._client.get.return_value = _ok_response({"platform": "linux"})
        assert client.server_platform() == "windows"  # cached


class TestWindowsPostContracts:
    def test_download_list_search_use_post_on_windows(self):
        client = _client("windows")
        client._client.post.return_value = _ok_response(
            {"items": []}, method="POST",
        )
        client._client.post.return_value.content = b"file-bytes"
        extra = {"paths": ["C:\\ws"]}
        data = client.download_bytes("sb", "C:\\ws\\a.txt", extra=extra)
        assert data == b"file-bytes"
        client.list_files(
            "sb", "C:\\ws", recursive=False, max_depth=None,
            include_files=True, include_dirs=True, extra=extra,
        )
        client.search_files("sb", "C:\\ws", "*.txt", None, extra=extra)
        posted = [call.args[0] for call in client._client.post.call_args_list]
        assert any(path.endswith("/download") for path in posted)
        assert any(path.endswith("/files/list") for path in posted)
        assert any(path.endswith("/files/search") for path in posted)
        client._client.get.assert_not_called()

    def test_download_list_search_use_get_on_linux(self):
        client = _client("linux")
        client._client.get.return_value = _ok_response({"items": []}, content=b"x")
        client.download_bytes("sb", "/tmp/a.txt", extra={"paths": ["/tmp"]})
        client.list_files(
            "sb", "/tmp", recursive=False, max_depth=None,
            include_files=True, include_dirs=True, extra={"paths": ["/tmp"]},
        )
        client.search_files("sb", "/tmp", "*.txt", None, extra={"paths": ["/tmp"]})
        client._client.post.assert_not_called()
        got = [call.args[0] for call in client._client.get.call_args_list]
        assert any("/download" in path for path in got)
        assert any(path.endswith("/files") for path in got)
        assert any(path.endswith("/search") for path in got)

    def test_exec_always_sends_extra(self):
        client = _client("linux")
        client._client.post.return_value = _ok_response({"exit_code": 0, "stdout": ""})
        client.exec("sb", ["echo"], extra={"paths": ["C:\\ws"]})
        body = client._client.post.call_args.kwargs["json"]
        assert body["extra"] == {"paths": ["C:\\ws"]}

    def test_upload_sends_multipart_extra_on_windows(self):
        client = _client("windows")
        client._client.post.return_value = _ok_response()
        client.upload_bytes("sb", "C:\\ws\\a.txt", b"hi", extra={"paths": ["C:\\ws"]})
        kwargs = client._client.post.call_args.kwargs
        assert "extra" in kwargs["data"]
        assert "C:\\\\ws" in kwargs["data"]["extra"] or "C:\\ws" in kwargs["data"]["extra"]
