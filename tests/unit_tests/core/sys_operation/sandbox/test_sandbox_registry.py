# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.core.sys_operation.sandbox.launchers.base import SandboxLauncher
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry


class TestSandboxRegistry:
    @staticmethod
    def test_register_and_create_launcher():
        class DummyLauncher(SandboxLauncher):
            pass

        name = "_test_registry_launcher"
        SandboxRegistry.register_launcher(name, DummyLauncher)
        try:
            launcher = SandboxRegistry.create_launcher(name)
            assert isinstance(launcher, DummyLauncher)
        finally:
            SandboxRegistry.unregister_launcher(name)

    @staticmethod
    def test_register_and_create_provider():
        class DummyProvider:
            def __init__(self, endpoint, config=None):
                self.endpoint = endpoint
                self.config = config

        sandbox_type = "_test_registry_sandbox"
        op_type = "fs"
        endpoint = SandboxEndpoint(base_url="http://localhost:8080")
        config = PreDeployLauncherConfig(base_url="http://localhost:8080")
        SandboxRegistry.register_provider(sandbox_type, op_type, DummyProvider)
        try:
            provider = SandboxRegistry.create_provider(sandbox_type, op_type, endpoint, config)
            assert isinstance(provider, DummyProvider)
            assert provider.endpoint == endpoint
            assert provider.config == config
        finally:
            SandboxRegistry.unregister_provider(sandbox_type, op_type)

    @staticmethod
    def test_create_launcher_unknown_type_raises():
        with pytest.raises(ValueError, match="Unknown launcher_type"):
            SandboxRegistry.create_launcher("_missing_launcher")

    @staticmethod
    def test_create_provider_unknown_type_raises():
        with pytest.raises(NotImplementedError, match="does not support operation"):
            SandboxRegistry.create_provider(
                "_missing_sandbox",
                "fs",
                SandboxEndpoint(base_url="http://localhost:8080"),
            )
