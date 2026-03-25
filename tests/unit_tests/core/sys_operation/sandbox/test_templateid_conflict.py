# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for sandbox isolation key template conflict detection.

These tests verify that when two sandbox operations have the same
isolation_key_template (same container_scope, launcher_type, sandbox_type, etc.),
the system correctly detects the conflict and prevents registration.
"""

import logging
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, SandboxGatewayConfig, SysOperation
from openjiuwen.core.sys_operation.config import SandboxIsolationConfig, PreDeployLauncherConfig, ContainerScope
from openjiuwen.core.sys_operation.sys_operation import SysOperationMgr

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


@pytest_asyncio.fixture(autouse=True)
async def reset_sys_op_mgr():
    """Reset SysOperationMgr singleton before and after each test."""
    SysOperationMgr.reset_instance()
    yield
    SysOperationMgr.reset_instance()


@pytest.mark.asyncio
class TestSandboxTemplateidConflict:
    """Test suite for sandbox isolation key template conflict detection."""

    @pytest_asyncio.fixture
    async def setup_runner(self):
        """Setup Runner before tests and stop after."""
        await Runner.start()
        yield
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_add_two_sandbox_ops_with_same_config_should_raise_conflict(
        self, setup_runner
    ):
        """Test that adding two sandbox operations with identical configs raises conflict error.

        Two sandbox operations with the same container_scope, launcher_type, sandbox_type,
        and isolation_prefix should have the same isolation_key_template, which should
        trigger a conflict error when trying to add the second operation.
        """
        card_id_1 = f"sandbox_op_1_{uuid.uuid4().hex[:8]}"
        card_id_2 = f"sandbox_op_2_{uuid.uuid4().hex[:8]}"

        # Both cards have identical sandbox configuration
        # This means they will generate the same isolation_key_template
        gateway_config = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card1 = SysOperationCard(
            id=card_id_1,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config,
        )

        # Add first card - should succeed
        with patch.object(SysOperation, '_validate_sandbox_gateway_config', _patched_validate):
            result1 = Runner.resource_mgr.add_sys_operation(card1)
            assert result1.is_ok(), f"First add_sys_operation should succeed, got: {result1.err()}"

        # Create second card with same configuration (same isolation_key_template)
        gateway_config_2 = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card2 = SysOperationCard(
            id=card_id_2,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config_2,
        )

        # Add second card with same sandbox config - should fail with conflict error
        with patch.object(SysOperation, '_validate_sandbox_gateway_config', _patched_validate):
            result2 = Runner.resource_mgr.add_sys_operation(card2)
            assert result2.is_err(), (
                "Second add_sys_operation with identical sandbox config should fail "
                "due to isolation key template conflict"
            )
            error_msg = str(result2.error())
            assert "conflict" in error_msg.lower() or "already registered" in error_msg.lower(), (
                f"Error message should mention conflict, got: {error_msg}"
            )

    @pytest.mark.asyncio
    async def test_add_two_sandbox_ops_with_different_container_scope_should_succeed(
        self, setup_runner
    ):
        """Test that adding two sandbox operations with different container_scope succeeds.

        Different container_scope values (e.g., SYSTEM vs SESSION) generate different
        isolation_key_templates, so no conflict should occur.
        """
        card_id_1 = f"sandbox_op_sys_{uuid.uuid4().hex[:8]}"
        card_id_2 = f"sandbox_op_sess_{uuid.uuid4().hex[:8]}"

        # First card: SYSTEM scope
        gateway_config_1 = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card1 = SysOperationCard(
            id=card_id_1,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config_1,
        )

        # Second card: SESSION scope (different from SYSTEM)
        gateway_config_2 = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SESSION),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card2 = SysOperationCard(
            id=card_id_2,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config_2,
        )

        with patch.object(SysOperation, '_validate_sandbox_gateway_config', _patched_validate):
            result1 = Runner.resource_mgr.add_sys_operation(card1)
            assert result1.is_ok(), f"First add_sys_operation should succeed, got: {result1.err()}"

            result2 = Runner.resource_mgr.add_sys_operation(card2)
            assert result2.is_ok(), (
                f"Second add_sys_operation with different container_scope should succeed, "
                f"got: {result2.err()}"
            )

    @pytest.mark.asyncio
    async def test_add_two_sandbox_ops_with_different_custom_id_should_succeed(
        self, setup_runner
    ):
        """Test that adding two sandbox operations with different custom_id succeeds.

        Same container_scope (CUSTOM) but different custom_id values generate different
        isolation_key_templates, so no conflict should occur.
        """
        card_id_1 = f"sandbox_op_custom1_{uuid.uuid4().hex[:8]}"
        card_id_2 = f"sandbox_op_custom2_{uuid.uuid4().hex[:8]}"

        # First card: CUSTOM scope with custom_id="custom_a"
        gateway_config_1 = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.CUSTOM,
                custom_id="custom_a"
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card1 = SysOperationCard(
            id=card_id_1,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config_1,
        )

        # Second card: CUSTOM scope with custom_id="custom_b" (different from custom_a)
        gateway_config_2 = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.CUSTOM,
                custom_id="custom_b"
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card2 = SysOperationCard(
            id=card_id_2,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config_2,
        )

        with patch.object(SysOperation, '_validate_sandbox_gateway_config', _patched_validate):
            result1 = Runner.resource_mgr.add_sys_operation(card1)
            assert result1.is_ok(), f"First add_sys_operation should succeed, got: {result1.err()}"

            result2 = Runner.resource_mgr.add_sys_operation(card2)
            assert result2.is_ok(), (
                f"Second add_sys_operation with different custom_id should succeed, "
                f"got: {result2.err()}"
            )

    @pytest.mark.asyncio
    async def test_add_two_sandbox_ops_with_different_prefix_should_succeed(
        self, setup_runner
    ):
        """Test that adding two sandbox operations with different isolation_prefix succeeds.

        Same container_scope but different isolation_prefix values generate different
        isolation_key_templates, so no conflict should occur.
        """
        card_id_1 = f"sandbox_op_prefix1_{uuid.uuid4().hex[:8]}"
        card_id_2 = f"sandbox_op_prefix2_{uuid.uuid4().hex[:8]}"

        # First card: prefix="agent1"
        gateway_config_1 = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.SYSTEM,
                prefix="agent1"
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card1 = SysOperationCard(
            id=card_id_1,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config_1,
        )

        # Second card: prefix="agent2" (different from agent1)
        gateway_config_2 = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.SYSTEM,
                prefix="agent2"
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card2 = SysOperationCard(
            id=card_id_2,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config_2,
        )

        with patch.object(SysOperation, '_validate_sandbox_gateway_config', _patched_validate):
            result1 = Runner.resource_mgr.add_sys_operation(card1)
            assert result1.is_ok(), f"First add_sys_operation should succeed, got: {result1.err()}"

            result2 = Runner.resource_mgr.add_sys_operation(card2)
            assert result2.is_ok(), (
                f"Second add_sys_operation with different prefix should succeed, "
                f"got: {result2.err()}"
            )

    @pytest.mark.asyncio
    async def test_add_same_sandbox_op_twice_should_succeed_idempotently(
        self, setup_runner
    ):
        """Test that adding the same sandbox operation card twice succeeds (idempotent).

        When the exact same operation_id is used, the conflict check should pass
        because the existing owner is the same operation.
        """
        card_id = f"sandbox_op_same_{uuid.uuid4().hex[:8]}"

        gateway_config = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        )

        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=gateway_config,
        )

        with patch.object(SysOperation, '_validate_sandbox_gateway_config', _patched_validate):
            result1 = Runner.resource_mgr.add_sys_operation(card)
            assert result1.is_ok(), f"First add_sys_operation should succeed, got: {result1.err()}"

            # Remove it first, then add the same card again
            Runner.resource_mgr.remove_sys_operation(card_id)
            result2 = Runner.resource_mgr.add_sys_operation(card)
            assert result2.is_ok(), (
                f"Re-adding the same operation after removal should succeed, got: {result2.err()}"
            )

    @pytest.mark.asyncio
    async def test_local_mode_operations_no_conflict_check(self, setup_runner):
        """Test that local mode operations do not trigger conflict checking.

        Local mode operations don't use isolation_key_template, so they should
        not be checked for conflicts.
        """
        card_id_1 = f"local_op_1_{uuid.uuid4().hex[:8]}"
        card_id_2 = f"local_op_2_{uuid.uuid4().hex[:8]}"

        card1 = SysOperationCard(
            id=card_id_1,
            mode=OperationMode.LOCAL,
        )

        card2 = SysOperationCard(
            id=card_id_2,
            mode=OperationMode.LOCAL,
        )

        result1 = Runner.resource_mgr.add_sys_operation(card1)
        assert result1.is_ok(), f"First add_sys_operation should succeed, got: {result1.err()}"

        result2 = Runner.resource_mgr.add_sys_operation(card2)
        assert result2.is_ok(), (
            f"Second local operation should succeed (no conflict check for local mode), "
            f"got: {result2.err()}"
        )