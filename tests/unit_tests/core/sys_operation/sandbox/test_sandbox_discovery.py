# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for sandbox operation discovery via OperationRegistry.

These tests verify that sandbox operations (fs, shell, code) are correctly
registered and discovered through the OperationRegistry.
"""

import pytest

from openjiuwen.core.sys_operation import OperationMode
from openjiuwen.core.sys_operation.registry import OperationRegistry


def test_sandbox_discovery():
    """Test that Sandbox operations are correctly discovered via OperationRegistry."""
    fs_op = OperationRegistry.get_operation_info("fs", OperationMode.SANDBOX)
    assert fs_op is not None
    assert fs_op.name == "fs"
    assert fs_op.mode == OperationMode.SANDBOX

    shell_op = OperationRegistry.get_operation_info("shell", OperationMode.SANDBOX)
    assert shell_op is not None
    assert shell_op.name == "shell"

    code_op = OperationRegistry.get_operation_info("code", OperationMode.SANDBOX)
    assert code_op is not None
    assert code_op.name == "code"
