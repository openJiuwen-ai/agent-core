# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import logging
import uuid

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, SandboxGatewayConfig
from openjiuwen.core.sys_operation.config import SandboxIsolationConfig, PreDeployLauncherConfig, ContainerScope

logger = logging.getLogger(__name__)

# Import local providers to register them
from tests.unit_tests.core.sys_operation.sandbox.providers import local_provider  # noqa: F401


@pytest_asyncio.fixture(name="local_op")
async def local_op_fixture():
    """Fixture that provides a SysOperationCard using local providers.

    This tests the SandboxRegistry provider registration and routing mechanism
    WITHOUT needing the AIO sandbox service.
    """
    from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxGateway

    card_id = f"local_sandbox_{uuid.uuid4().hex[:8]}"

    # Ensure aio providers are registered (they are registered on first SandboxGateway init)
    SandboxGateway.get_instance()

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
