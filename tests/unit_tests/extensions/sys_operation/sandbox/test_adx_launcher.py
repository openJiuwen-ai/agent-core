"""ADX registration and launcher teardown preserve the cutover contracts."""

from unittest.mock import AsyncMock, MagicMock

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


@pytest.fixture
def adx_cleanup_cache(monkeypatch):
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import _AdxEnvironmentBackend

    monkeypatch.setattr(_AdxEnvironmentBackend, "_instances", {})
    monkeypatch.setattr(_AdxEnvironmentBackend, "_incarnations", {})
    return _AdxEnvironmentBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_url", ["http://localhost:18080", "http://first-node:18080"])
@pytest.mark.parametrize("cleanup", ["launcher", "base_url"])
async def test_cleanup_reaches_changed_node_endpoints_without_deleting_other_scopes(
    monkeypatch, tmp_path, adx_cleanup_cache, initial_url, cleanup
):
    import json

    import adx_environment

    from openjiuwen.core.sys_operation.sandbox.launchers.pre_deployment_launcher import PreDeploymentLauncher
    from openjiuwen.extensions.sys_operation.sandbox.providers.adx_environment import (
        AdxEnvironmentFSProvider,
        delete_adx_environment,
    )

    instances = [MagicMock(id=f"cleanup-node-{index}") for index in range(4)]
    factory = MagicMock(side_effect=instances)
    monkeypatch.setattr(adx_environment, "Environment", factory)
    context = tmp_path / "node.json"
    config = provider_config()
    config.launcher_config.extra_params["endpoint_file"] = str(context)
    endpoint = SandboxEndpoint(base_url="http://localhost:18080", isolation_key="cleanup-owner")
    provider = AdxEnvironmentFSProvider(endpoint, config)
    for index, node in enumerate(
        [
            {"ingress_url": initial_url, "node_id": "first"},
            {"ingress_url": "http://second-node:18080", "node_id": "second"},
            {"ingress_url": "http://second-node:18080", "node_id": "replacement"},
        ]
    ):
        context.write_text(json.dumps(node))
        assert provider._get_environment() is instances[index]
    assert len({call.kwargs["name"] for call in factory.call_args_list}) == 3

    other_url = endpoint.base_url if cleanup == "launcher" else "http://other-origin:18080"
    other = AdxEnvironmentFSProvider(SandboxEndpoint(base_url=other_url, isolation_key="other-owner"), config)
    assert other._get_environment() is instances[3]
    if cleanup == "launcher":
        await PreDeploymentLauncher().delete(
            "", sandbox_type="adx-environment", base_url=endpoint.base_url, isolation_key=endpoint.isolation_key
        )
    else:
        await delete_adx_environment(base_url=endpoint.base_url)

    assert [call.args[0] for call in factory.delete.call_args_list] == [item.id for item in instances[:3]]
    for item in instances[:3]:
        item.close.assert_called_once()
    instances[3].close.assert_not_called()
    assert len(adx_cleanup_cache._instances) == 1


def test_adx_registration_matches_the_release_and_preserves_legacy_config():
    from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry
    from openjiuwen.extensions.sys_operation.sandbox.providers import adx_environment, yuanrong

    providers = {
        "fs": (adx_environment.AdxEnvironmentFSProvider, yuanrong.YuanrongFSProvider),
        "shell": (adx_environment.AdxEnvironmentShellProvider, yuanrong.YuanrongShellProvider),
        "code": (adx_environment.AdxEnvironmentCodeProvider, yuanrong.YuanrongCodeProvider),
    }
    for operation, (sdk_provider, legacy_provider) in providers.items():
        assert SandboxRegistry.get_provider_cls("adx-environment", operation) is sdk_provider
        assert SandboxRegistry.get_provider_cls("yuanrong", operation) is legacy_provider


@pytest.mark.asyncio
async def test_legacy_launcher_keeps_its_provider_cleanup(monkeypatch):
    from openjiuwen.core.sys_operation.sandbox.launchers.pre_deployment_launcher import PreDeploymentLauncher
    from openjiuwen.extensions.sys_operation.sandbox.providers import yuanrong

    cleanup = AsyncMock()
    monkeypatch.setattr(yuanrong, "delete_yuanrong_sandbox", cleanup)
    base_url = "http://localhost:18080"
    await PreDeploymentLauncher().delete("", sandbox_type="yuanrong", base_url=base_url, isolation_key="legacy")
    cleanup.assert_awaited_once_with(
        shared_key=yuanrong.build_yuanrong_shared_scope_key(base_url, "legacy"),
        base_url=None,
        reason="teardown",
    )
