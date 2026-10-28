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


def test_idle_deleted_environment_is_replaced_before_new_work(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentFSProvider

    first, second = MagicMock(), MagicMock()
    first.get_info.return_value = SimpleNamespace(state="stopped")
    factory = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(adx_environment, "Environment", factory)
    provider = AdxEnvironmentFSProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="idle-deleted"), provider_config()
    )
    assert provider._get_environment() is first
    assert provider._get_environment() is second
    first.close.assert_called_once()
    assert factory.call_args_list[0].kwargs["name"] != factory.call_args_list[1].kwargs["name"]


def test_unknown_environment_state_does_not_create_or_replay(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    first = MagicMock()
    first.get_info.side_effect = TimeoutError("directory unavailable")
    factory = MagicMock(return_value=first)
    monkeypatch.setattr(adx_environment, "Environment", factory)
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="idle-unknown"), provider_config()
    )
    assert provider._get_environment() is first
    with pytest.raises(TimeoutError):
        provider._command_sync("append once")
    assert factory.call_count == 1
    first.commands.run.assert_not_called()


def test_conch_serialized_traffic_policy_is_passed_to_public_sdk(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(
        {
            "runtime": "conch",
            "cpu": 1000,
            "cpu_limit": 1000,
            "network": {
                "traffic": {
                    "mode": "stateful",
                    "ingress_default_action": "allow",
                    "egress_default_action": "deny",
                    "rules": [
                        {
                            "direction": "egress",
                            "action": "allow",
                            "protocol": "tcp",
                            "cidr": "10.0.0.0/24",
                            "port_range": {"first": 443, "last": 443},
                        }
                    ],
                }
            },
        }
    )
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="conch-traffic"), config
    )
    provider._get_environment()
    arguments = factory.call_args.kwargs
    assert arguments["runtime"] == "conch"
    policy = arguments["network"].traffic
    assert policy.mode == "stateful"
    assert policy.egress_default_action == "deny"
    assert policy.ingress_default_action == "allow"
    assert policy.rules[0].cidr == "10.0.0.0/24"
    assert policy.rules[0].port_range.first == 443
    # Parsing must not replace the serializable deployment configuration.
    assert isinstance(config.launcher_config.extra_params["network"]["traffic"], dict)


@pytest.mark.asyncio
async def test_shell_code_and_fs_share_environment_without_replaying_commands(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import (
        AdxEnvironmentCodeProvider,
        AdxEnvironmentFSProvider,
        AdxEnvironmentShellProvider,
        build_adx_shared_scope_key,
        delete_adx_environment,
    )

    instance = MagicMock()
    instance.id = "default-test-tools"
    handle = MagicMock()
    handle.wait.return_value = SimpleNamespace(exit_code=0, stdout="hello\n", stderr="")
    instance.commands.run.return_value = handle
    instance.files.read.return_value = "first\nsecond\n"
    factory = MagicMock(return_value=instance)
    monkeypatch.setattr(adx_environment, "Environment", factory)
    endpoint = SandboxEndpoint(base_url="http://localhost:18080", isolation_key="agent-a")
    shell = AdxEnvironmentShellProvider(endpoint, provider_config())
    code = AdxEnvironmentCodeProvider(endpoint, provider_config())
    fs = AdxEnvironmentFSProvider(endpoint, provider_config())
    try:
        result = await shell.execute_cmd("printf hello", cwd="/home/jiuwen", environment={"KEY": "value"})
        assert result.data.stdout == "hello\n"
        await code.execute_code("print('hello')")
        read = await fs.read_file("/home/jiuwen/a.txt", head=1)
        assert read.data.content == "first\n"
        assert factory.call_count == 1
        assert factory.call_args.kwargs["network"].block_network is True
        mount = factory.call_args.kwargs["mounts"][0].to_dict()
        assert mount["host_path"] == "/home/agentos/users/u1"
        assert mount["options"] == ["rw"]
        kwargs = instance.commands.run.call_args_list[0].kwargs
        assert kwargs["background"] is True
        assert kwargs["command_id"]
        assert kwargs["cwd"] == "/home/jiuwen"
        assert kwargs["envs"] == {"KEY": "value"}
        instance.commands.run.side_effect = RuntimeError("unknown outcome")
        before = instance.commands.run.call_count
        failed = await shell.execute_cmd("append something")
        assert failed.data is None or failed.data.exit_code != 0
        assert instance.commands.run.call_count == before + 1
    finally:
        await delete_adx_environment(shared_key=build_adx_shared_scope_key(endpoint.base_url, "agent-a"))
    factory.delete.assert_called_once_with(instance.id, connection=factory.call_args.kwargs["connection"])
    instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_distinct_agents_can_mount_same_workspace(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import (
        AdxEnvironmentShellProvider,
        delete_adx_environment,
    )

    factory = MagicMock()
    factory.return_value.commands.run.return_value.wait.return_value = SimpleNamespace(
        exit_code=0, stdout="", stderr=""
    )
    monkeypatch.setattr(adx_environment, "Environment", factory)
    for key in ("agent-a", "agent-b"):
        provider = AdxEnvironmentShellProvider(
            SandboxEndpoint(base_url="http://localhost:18080", isolation_key=key), provider_config()
        )
        await provider.execute_cmd("true")
    assert factory.call_count == 2
    calls = factory.call_args_list
    assert calls[0].kwargs["name"] != calls[1].kwargs["name"]
    assert calls[0].kwargs["mounts"] == calls[1].kwargs["mounts"]
    await delete_adx_environment(base_url="http://localhost:18080")


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


@pytest.mark.asyncio
async def test_truncated_output_is_visible_to_tool_caller_without_replay(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    provider = AdxEnvironmentShellProvider(SandboxEndpoint(base_url="http://localhost:18080"), provider_config())
    instance = MagicMock()
    instance.id = "tool-env"
    instance.commands.run.return_value.wait.return_value = SimpleNamespace(
        exit_code=0, stdout="partial", stderr="", truncated=True, stdout_truncated=True, stderr_truncated=False
    )
    monkeypatch.setattr(provider, "_get_environment", lambda: instance)
    result = await provider.execute_cmd("large-output")
    assert result.data.stdout == "partial"
    assert "truncated" in result.data.stderr
    assert "command_id=" in result.data.stderr
    assert "environment_id=tool-env" in result.data.stderr
    instance.commands.run.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error_code,error_message",
    [
        ("FAILED", "SPAWN_FAILED", "working directory does not exist"),
        ("RUNNING", "WAIT_TIMEOUT", "command wait timed out"),
    ],
)
async def test_missing_command_exit_code_reports_outcome_without_replay(monkeypatch, status, error_code, error_message):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    provider = AdxEnvironmentShellProvider(SandboxEndpoint(base_url="http://localhost:18080"), provider_config())
    instance = MagicMock()
    instance.id = "tool-env"
    instance.commands.run.return_value.wait.return_value = SimpleNamespace(
        exit_code=None,
        stdout="",
        stderr="",
        status=status,
        error_code=error_code,
        error_message=error_message,
    )
    monkeypatch.setattr(provider, "_get_environment", lambda: instance)
    result = await provider.execute_cmd("printf once")
    assert result.data is None
    assert error_code in result.message
    assert "command_id=" in result.message
    instance.commands.run.assert_called_once()


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


@pytest.mark.parametrize("user,group", [("1001", "1002"), (1001, 0)])
def test_tool_user_group_reach_environment_create(monkeypatch, user, group):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(runtime="conch", user=user, group=group)
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="tool-uid"), config
    )
    provider._get_environment()
    assert factory.call_args.kwargs["user"] == str(user)
    assert factory.call_args.kwargs["group"] == str(group)


@pytest.mark.parametrize("user,group", [(True, "1002"), ("1001", ""), ("1001", None)])
def test_invalid_tool_user_group_is_rejected(user, group):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    config = provider_config()
    config.launcher_config.extra_params.update(user=user, group=group)
    with pytest.raises(ValueError):
        AdxEnvironmentShellProvider(SandboxEndpoint(base_url="http://localhost:18080"), config)


def test_identity_change_cannot_reuse_another_uid_environment(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    endpoint = SandboxEndpoint(base_url="http://localhost:18080", isolation_key="changed-tool-uid")
    for uid in ("1001", "1003"):
        config = provider_config()
        config.launcher_config.extra_params.update(runtime="conch", user=uid, group="1002")
        AdxEnvironmentShellProvider(endpoint, config)._get_environment()
    assert factory.call_count == 2
    assert factory.call_args_list[0].kwargs["name"] != factory.call_args_list[1].kwargs["name"]


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
