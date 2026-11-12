# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public access to the effective local operation configuration."""

import pytest

from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SandboxGatewayConfig,
    SysOperation,
    SysOperationCard,
)
from openjiuwen.core.sys_operation.config import SandboxLauncherConfig


@pytest.mark.parametrize("configured", [False, True])
def test_work_config_returns_effective_local_configuration(configured):
    config = LocalWorkConfig(restrict_to_sandbox=True, sandbox_root=["workspace"]) if configured else None
    card = SysOperationCard(id="local-config", mode=OperationMode.LOCAL, work_config=config)
    operation = SysOperation(card)

    assert operation.work_config == (config or LocalWorkConfig())
    if configured:
        assert operation.work_config is card.work_config


def test_work_config_is_a_read_only_property():
    operation = SysOperation(SysOperationCard(id="read-only-config", mode=OperationMode.LOCAL))

    with pytest.raises(AttributeError):
        operation.work_config = LocalWorkConfig()


def test_sandbox_operation_has_no_local_work_config():
    operation = SysOperation(SysOperationCard(
        id="sandbox-config",
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            launcher_config=SandboxLauncherConfig(launcher_type="mock", sandbox_type="mock"),
        ),
    ))

    assert operation.work_config is None
