# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
conftest_local.py - Local provider fixtures for testing SandboxRegistry mechanism.

This conftest provides a fixture that:
1. Registers local providers (LocalFSProvider, LocalShellProvider, LocalCodeProvider)
   under sandbox_type="local" - completely separate from "aio"
2. Patches phase-1 validation to accept sandbox_type="local"
3. Creates a SysOperationCard with sandbox_type="local"

No real HTTP service needed. No mock of BaseProvider classes.
Tests verify the provider registration/routing mechanism works correctly.
"""

import logging
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, SandboxGatewayConfig
from openjiuwen.core.sys_operation.config import SandboxIsolationConfig, PreDeployLauncherConfig

# Import local providers to register them
from tests.unit_tests.core.sys_operation.sandbox.providers import local_provider  # noqa: F401

logger = logging.getLogger(__name__)


def _patched_validate(self, gateway_config):
    """Patched version of _validate_sandbox_gateway_config that accepts 'local' sandbox_type."""
    config = gateway_config or SandboxGatewayConfig()
    launcher_config = config.launcher_config
    if launcher_config is None:
        from openjiuwen.core.common.exception import build_error
        from openjiuwen.core.common.exception.codes import StatusCode
        raise build_error(StatusCode.SYS_OPERATION_CARD_PARAM_ERROR,
                         error_msg="sandbox mode requires launcher_config")
    if launcher_config.launcher_type != "pre_deploy":
        from openjiuwen.core.common.exception import build_error
        from openjiuwen.core.common.exception.codes import StatusCode
        raise build_error(StatusCode.SYS_OPERATION_CARD_PARAM_ERROR,
                         error_msg=f"only pre_deploy launcher is supported, current: {launcher_config.launcher_type}")
    # Accept both "aio" and "local" sandbox_type
    if launcher_config.sandbox_type not in ("aio", "local"):
        from openjiuwen.core.common.exception import build_error
        from openjiuwen.core.common.exception.codes import StatusCode
        raise build_error(StatusCode.SYS_OPERATION_CARD_PARAM_ERROR,
                         error_msg=f"only aio and local sandbox_type are supported, "
                                   f"current: {launcher_config.sandbox_type}")
    return config


@pytest_asyncio.fixture(name="local_op")
async def local_op_fixture():
    """Fixture that provides a SysOperationCard using local providers.

    This tests the SandboxRegistry provider registration and routing mechanism
    WITHOUT needing the AIO sandbox service or writing MockProvider classes.
    """
    from openjiuwen.core.sys_operation.sys_operation import SysOperation
    from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxGateway

    card_id = f"local_sandbox_{uuid.uuid4().hex[:8]}"

    # Ensure aio providers are registered (they are registered on first SandboxGateway init)
    SandboxGateway.get_instance()

    # Patch the staticmethod _validate_sandbox_gateway_config on SysOperation class
    with patch.object(SysOperation, '_validate_sandbox_gateway_config', _patched_validate):
        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(container_scope="system"),
                launcher_config=PreDeployLauncherConfig(
                    base_url="http://local-provider:9999",  # Won't be called
                    sandbox_type="local",  # Uses local providers
                    idle_ttl_seconds=600,
                ),
                timeout_seconds=30,
            )
        )

        try:
            await Runner.start()
            Runner.resource_mgr.add_sys_operation(card)
            op = Runner.resource_mgr.get_sys_operation(card_id)
            yield op
        finally:
            Runner.resource_mgr.remove_sys_operation(card_id)
            await Runner.stop()
