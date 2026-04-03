# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation import OperationMode, SysOperationCard
from openjiuwen.core.sys_operation.config import (
    PreDeployLauncherConfig,
    SandboxGatewayConfig,
    SandboxIsolationConfig,
)
from openjiuwen.core.sys_operation.sys_operation import SysOperation


class TestSandboxPhase1Validation:
    @staticmethod
    def test_pre_deploy_aio_config_is_allowed():
        card = SysOperationCard(
            id="sandbox_ok",
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(container_scope="system"),
                launcher_config=PreDeployLauncherConfig(base_url="http://localhost:8080", sandbox_type="aio"),
            ),
        )

        op = SysOperation(card)
        assert op.mode == OperationMode.SANDBOX

    @staticmethod
    def test_missing_launcher_config_is_rejected():
        card = SysOperationCard(
            id="sandbox_missing_launcher",
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(container_scope="system"),
            ),
        )

        with pytest.raises(Exception, match="sandbox mode requires launcher_config") as exc_info:
            SysOperation(card)
        assert exc_info.value.code == StatusCode.SYS_OPERATION_CARD_PARAM_ERROR.code

    @staticmethod
    def test_missing_sandbox_type_is_rejected():
        card = SysOperationCard(
            id="sandbox_missing_sandbox_type",
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(container_scope="system"),
                launcher_config=PreDeployLauncherConfig(base_url="http://localhost:8080", sandbox_type=""),
            ),
        )

        with pytest.raises(Exception, match="sandbox mode requires sandbox_type") as exc_info:
            SysOperation(card)
        assert exc_info.value.code == StatusCode.SYS_OPERATION_CARD_PARAM_ERROR.code
