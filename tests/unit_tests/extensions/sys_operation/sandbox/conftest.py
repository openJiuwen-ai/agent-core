# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fixtures for real AIO sandbox integration tests.

These tests require:
- A running AIO sandbox service at http://localhost:8080
- For agent tests: proper LLM configuration
"""

import logging
import os
import socket
import uuid
from urllib.parse import urlparse

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, SandboxGatewayConfig
from openjiuwen.core.sys_operation.config import SandboxIsolationConfig, PreDeployLauncherConfig, ContainerScope

logger = logging.getLogger(__name__)


def _is_truthy_env(value: str) -> bool:
    return value.lower() in {"1", "true", "yes"}


def _aio_sandbox_is_reachable(base_url: str = "http://localhost:8080") -> bool:
    """Check whether the configured AIO sandbox endpoint is reachable."""
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _require_real_aio_sandbox() -> None:
    """Skip real AIO integration tests unless service is reachable or explicitly enabled."""
    env_value = os.environ.get("RUN_REAL_AIO_SANDBOX_TESTS", "")
    if _is_truthy_env(env_value) or _aio_sandbox_is_reachable():
        return
    pytest.skip(
        "Requires running AIO sandbox on http://localhost:8080; "
        "start the service or set RUN_REAL_AIO_SANDBOX_TESTS=1 to force-enable."
    )


@pytest_asyncio.fixture(name="real_aio_op")
async def real_aio_op_fixture():
    """Fixture that provides a real SysOperationCard connected to AIO sandbox at localhost:8080."""
    _require_real_aio_sandbox()
    await Runner.start()
    card_id = f"real_aio_sandbox_{uuid.uuid4().hex[:8]}"
    card = SysOperationCard(
        id=card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )
    )
    try:
        Runner.resource_mgr.add_sys_operation(card)
        op = Runner.resource_mgr.get_sys_operation(card_id)
        yield op
    finally:
        Runner.resource_mgr.remove_sys_operation(card_id)
        await Runner.stop()


@pytest_asyncio.fixture(name="aio_agent_op")
async def aio_agent_op_fixture():
    """Fixture that provides a SysOperationCard connected to AIO sandbox at localhost:8080.

    This fixture is designed for Agent integration tests where tools need to be
    mounted onto a ReActAgent.
    """
    _require_real_aio_sandbox()
    card_id = f"aio_agent_sandbox_{uuid.uuid4().hex[:8]}"

    card = SysOperationCard(
        id=card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )
    )

    try:
        await Runner.start()
        result = Runner.resource_mgr.add_sys_operation(card)
        if not result.is_ok():
            raise RuntimeError(f"Failed to add sys_operation: {result.err()}")
        op = Runner.resource_mgr.get_sys_operation(card_id)
        yield op
    finally:
        Runner.resource_mgr.remove_sys_operation(card_id)
        await Runner.stop()
