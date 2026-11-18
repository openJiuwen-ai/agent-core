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



@pytest.fixture
def adx_cleanup_cache(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import _AdxEnvironmentBackend

    monkeypatch.setattr(_AdxEnvironmentBackend, "_instances", {})
    monkeypatch.setattr(_AdxEnvironmentBackend, "_incarnations", {})
    return _AdxEnvironmentBackend


def test_idle_pause_configuration_reaches_public_sdk(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(
        idle_timeout=900,
        idle_action="pause",
        pause_policy={
            "retention": {"kind": "ttl", "ttlSeconds": 86400},
            "timeoutSeconds": 300,
            "storage": "configured",
        },
        failover=True,
    )
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="idle-pause"), config
    )
    provider._get_environment()
    arguments = factory.call_args.kwargs
    assert arguments["idle_action"] is adx_environment.IdleAction.PAUSE
    assert arguments["idle_timeout"] == 900
    assert arguments["failover"] is True
    assert arguments["pause_policy"] == adx_environment.PausePolicy(
        retention=adx_environment.RetentionPolicy.for_seconds(86400),
        timeout_seconds=300,
        storage=adx_environment.CheckpointStorage.CONFIGURED,
    )


def test_permanent_pause_retention_and_inherited_defaults(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(
        idle_timeout=900, idle_action="pause", pause_policy={"retention": {"kind": "forever"}}
    )
    AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="idle-pause-forever"), config
    )._get_environment()
    policy = factory.call_args.kwargs["pause_policy"]
    assert policy.retention.to_dict() == {"kind": "forever"}
    assert policy.timeout_seconds == 300
    assert policy.storage is adx_environment.CheckpointStorage.CONFIGURED


@pytest.mark.parametrize(
    "policy",
    [
        "pause",
        {"retention": {"kind": "never"}},
        {"retention": {"kind": "ttl"}},
        {"retention": {"kind": "ttl", "ttlSeconds": True}},
        {"retention": {"kind": "forever", "ttlSeconds": 5}},
        {"timeoutSeconds": "300"},
        {"storage": "cluster"},
        {"timeout_seconds": 300},
    ],
)
def test_invalid_pause_policy_is_rejected_before_create(monkeypatch, policy):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(idle_timeout=900, idle_action="pause", pause_policy=policy)
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="invalid-pause-policy"), config
    )
    with pytest.raises(ValueError):
        provider._get_environment()
    factory.assert_not_called()


@pytest.mark.parametrize("action", ["PAUSE", "", True, 1, None])
def test_invalid_idle_action_is_rejected_before_create(monkeypatch, action):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(idle_timeout=900, idle_action=action)
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key=f"invalid-idle-action-{action}"), config
    )
    with pytest.raises(ValueError):
        provider._get_environment()
    factory.assert_not_called()


def test_idle_pause_is_rejected_for_conch_runtime(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(runtime="conch", idle_timeout=900, idle_action="pause")
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="conch-idle-pause"), config
    )
    with pytest.raises(ValueError, match="conch"):
        provider._get_environment()
    factory.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, True, 60.0])
def test_idle_pause_requires_positive_integer_timeout(monkeypatch, timeout):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(idle_timeout=timeout, idle_action="pause")
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key=f"idle-timeout-{timeout}"), config
    )
    with pytest.raises(ValueError, match="idle_timeout"):
        provider._get_environment()
    factory.assert_not_called()


def test_idle_pause_inherits_sdk_default_timeout(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params.update(idle_action="pause")
    AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="idle-default-timeout"), config
    )._get_environment()
    assert "idle_timeout" not in factory.call_args.kwargs
    assert factory.call_args.kwargs["idle_action"] is adx_environment.IdleAction.PAUSE


@pytest.mark.parametrize("failover", ["true", 1, "yes"])
def test_failover_must_be_boolean(monkeypatch, failover):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    config = provider_config()
    config.launcher_config.extra_params["failover"] = failover
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key=f"failover-{failover}"), config
    )
    with pytest.raises(ValueError, match="failover"):
        provider._get_environment()
    factory.assert_not_called()


def test_paused_environment_is_reused_and_not_recreated(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    instance = MagicMock(id="tools-paused")
    instance.get_info.return_value = SimpleNamespace(state="paused")
    factory = MagicMock(return_value=instance)
    monkeypatch.setattr(adx_environment, "Environment", factory)
    provider = AdxEnvironmentShellProvider(
        SandboxEndpoint(base_url="http://localhost:18080", isolation_key="paused-reuse"), provider_config()
    )
    assert provider._get_environment() is instance
    assert provider._get_environment() is instance
    assert factory.call_count == 1
    instance.close.assert_not_called()


def test_default_lifecycle_settings_do_not_change_environment_scope():
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import _lifecycle_signature

    assert _lifecycle_signature({}) == ""
    assert _lifecycle_signature({"idle_action": "delete"}) == ""
    assert _lifecycle_signature({"failover": False}) == ""
    assert _lifecycle_signature({"idle_action": "pause", "idle_timeout": 900}) != _lifecycle_signature({})
    assert _lifecycle_signature({"failover": True}) != _lifecycle_signature({})


def test_lifecycle_change_selects_a_new_environment(monkeypatch):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import AdxEnvironmentShellProvider

    factory = MagicMock()
    monkeypatch.setattr(adx_environment, "Environment", factory)
    endpoint = SandboxEndpoint(base_url="http://localhost:18080", isolation_key="lifecycle-change")
    for storage in ("local", "shared"):
        config = provider_config()
        config.launcher_config.extra_params.update(
            idle_timeout=900, idle_action="pause", pause_policy={"storage": storage}
        )
        AdxEnvironmentShellProvider(endpoint, config)._get_environment()
    assert factory.call_count == 2
    assert factory.call_args_list[0].kwargs["name"] != factory.call_args_list[1].kwargs["name"]


def test_cleanup_matches_lifecycle_scoped_environment(monkeypatch, adx_cleanup_cache):
    import adx_environment

    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import (
        AdxEnvironmentFSProvider,
        build_adx_shared_scope_key,
    )

    instance = MagicMock(id="tools-lifecycle-cleanup")
    factory = MagicMock(return_value=instance)
    monkeypatch.setattr(adx_environment, "Environment", factory)
    endpoint = SandboxEndpoint(base_url="http://localhost:18080", isolation_key="lifecycle-cleanup")
    config = provider_config()
    config.launcher_config.extra_params.update(idle_timeout=900, idle_action="pause")
    assert AdxEnvironmentFSProvider(endpoint, config)._get_environment() is instance
    deleted = adx_cleanup_cache.delete_environments(
        build_adx_shared_scope_key(endpoint.base_url, endpoint.isolation_key), None
    )
    assert len(deleted) == 1
    factory.delete.assert_called_once_with(instance.id, connection=factory.call_args.kwargs["connection"])
    instance.close.assert_called_once()
