"""Native ADX provider contracts; no backend service is required."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig, SandboxGatewayConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint


def provider_config():
    return SandboxGatewayConfig(
        launcher_config=PreDeployLauncherConfig(
            base_url="http://localhost:18080",
            sandbox_type="adx-environment",
            extra_params={
                "connection": {"token": "test-key"},
                "image": "tools:v1",
                "network": {"block_network": True},
                "runtime": "runsc",
                "mounts": [{"host_path": "/home/agentos/users/u1", "target": "/home/jiuwen", "options": ["rw"]}],
            },
        )
    )


def test_tool_identity_includes_agent_instance_not_only_project(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import build_adx_shared_scope_key

    monkeypatch.setenv("JIUWENSWARM_ADX_AGENT_KEY", "adx-agent-a")
    first = build_adx_shared_scope_key("https://adx.local", "project_default")
    monkeypatch.setenv("JIUWENSWARM_ADX_AGENT_KEY", "adx-agent-b")
    second = build_adx_shared_scope_key("https://adx.local", "project_default")
    assert first != second


def test_registration_uses_adx_for_all_tool_operations():
    import openjiuwen.extensions.sys_operation.sandbox.providers  # noqa: F401 -- registers providers
    from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry

    for operation in ("shell", "code", "fs"):
        assert SandboxRegistry.get_provider_cls("adx-environment", operation) is not None
        with pytest.raises(NotImplementedError):
            SandboxRegistry.create_provider("yuanrong", operation, SandboxEndpoint(base_url="http://unused"))


def test_listing_depth_and_file_root_contract(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentFSProvider

    provider = AdxEnvironmentFSProvider(SandboxEndpoint(base_url="http://localhost:18080"), provider_config())
    instance = MagicMock()
    instance.files.list.return_value = []
    monkeypatch.setattr(provider, "_get_environment", lambda: instance)
    assert provider._fs_list_sync("/tree", recursive=True, max_depth=2) == []
    instance.files.list.assert_called_once_with("/tree", depth=3)
    instance.files.list.side_effect = NotADirectoryError("/file")
    assert provider._fs_list_sync("/file") == []


def test_search_missing_root_does_not_hide_permission_errors(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentFSProvider

    provider = AdxEnvironmentFSProvider(SandboxEndpoint(base_url="http://localhost:18080"), provider_config())
    instance = MagicMock()
    monkeypatch.setattr(provider, "_get_environment", lambda: instance)
    instance.files.list.side_effect = FileNotFoundError("/missing")
    instance.files.search.return_value = []
    assert provider._fs_search_sync("/missing", "*") == []
    with pytest.raises(FileNotFoundError, match="path not found: /missing"):
        provider._fs_list_sync("/missing")
    instance.files.search.side_effect = PermissionError("/private")
    with pytest.raises(PermissionError, match="/private"):
        provider._fs_search_sync("/private", "*")


def test_search_delegates_matching_and_exclusions_to_adx(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentFSProvider

    provider = AdxEnvironmentFSProvider(SandboxEndpoint(base_url="http://localhost:18080"), provider_config())
    instance = MagicMock()
    instance.files.search.return_value = [
        SimpleNamespace(name="a.py", path="/tree/a.py", type="file", size=2, modified_time=0.0)
    ]
    monkeypatch.setattr(provider, "_get_environment", lambda: instance)
    result = provider._fs_search_sync("/tree", "*.py", ["test_*"])
    assert [i["path"] for i in result] == ["/tree/a.py"]
    instance.files.search.assert_called_once_with("/tree", "*.py", exclude_patterns=["test_*"])
    instance.files.list.assert_not_called()


@pytest.mark.parametrize("user,group", [(True, "1002"), ("1001", ""), ("1001", None)])
def test_invalid_tool_user_group_is_rejected(user, group):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    config = provider_config()
    config.launcher_config.extra_params.update(user=user, group=group)
    with pytest.raises(ValueError):
        AdxEnvironmentShellProvider(SandboxEndpoint(base_url="http://localhost:18080"), config)


@pytest.mark.asyncio
async def test_local_file_streams_preserve_bytes_and_report_size(monkeypatch, tmp_path):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentFSProvider

    provider = AdxEnvironmentFSProvider(SandboxEndpoint(base_url="http://localhost:18080"), provider_config())
    instance = MagicMock()
    instance.files.exists.return_value = False
    payload = b"\x00native-file-content\xff"
    instance.files.read.return_value = payload
    monkeypatch.setattr(provider, "_get_environment", lambda: instance)
    source = tmp_path / "source.bin"
    source.write_bytes(payload)

    uploads = [chunk async for chunk in provider.upload_file_stream(str(source), "/workspace/data.bin")]
    assert len(uploads) == 1 and uploads[0].data.is_last_chunk
    assert uploads[0].data.chunk_size == len(payload)
    instance.files.write.assert_called_once_with("/workspace/data.bin", payload)

    target = tmp_path / "nested" / "download.bin"
    downloads = [chunk async for chunk in provider.download_file_stream("/workspace/data.bin", str(target))]
    assert target.read_bytes() == payload
    assert len(downloads) == 1 and downloads[0].data.is_last_chunk
    assert downloads[0].data.chunk_size == len(payload)


@pytest.mark.parametrize(
    "rootfs",
    [
        {},
        {"type": "local"},
        {"type": "local", "path": 1},
        {"type": "local", "path": "relative"},
        {"type": "local", "path": "/node/../rootfs"},
    ],
)
def test_local_rootfs_config_errors_are_reported_before_operations(rootfs):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentFSProvider

    config = provider_config()
    config.launcher_config.extra_params["rootfs"] = rootfs
    with pytest.raises(ValueError, match="rootfs"):
        AdxEnvironmentFSProvider(SandboxEndpoint(base_url="http://localhost:18080"), config)
