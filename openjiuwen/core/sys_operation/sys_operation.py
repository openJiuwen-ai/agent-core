# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, List

from pydantic import Field, field_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.schema import BaseCard
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.local.config import LocalWorkConfig
from openjiuwen.core.sys_operation.registry import OperationRegistry
from openjiuwen.core.sys_operation.sandbox.config import SandboxGatewayConfig


class SysOperationCard(BaseCard):
    mode: OperationMode = Field(
        default=OperationMode.LOCAL,
        description="Running mode, available values: local / sandbox"
    )
    work_config: Optional[LocalWorkConfig] = Field(
        default=None,
        description="Local work config (required when mode is local)"
    )
    gateway_config: Optional[SandboxGatewayConfig] = Field(
        default=None,
        description="Sandbox gateway config (required when mode is sandbox)"
    )

    @classmethod
    @field_validator("mode")
    def mode_must_be_valid_enum(cls, v):
        """Validate that mode is a valid value in OperationMode enum"""
        if not isinstance(v, OperationMode):
            try:
                return OperationMode(v.lower())
            except ValueError as ex:
                raise build_error(StatusCode.SYS_OPERATION_CARD_PARAM_ERROR,
                    error_msg=f"mode must be one of {[e.value for e in OperationMode]}, current value: {v}",
                    cause=ex) from ex
        return v


class SysOperation:
    """SysOperation"""

    def __init__(self, card: SysOperationCard):
        self.mode = card.mode
        if self.mode == OperationMode.LOCAL:
            self._run_config = card.work_config or LocalWorkConfig()
        else:
            self._run_config = card.gateway_config or SandboxGatewayConfig()
        self._instances = {}

    def __getattr__(self, name):
        return self._get_operation(name)

    def fs(self):
        return self._get_operation("fs")

    def code(self):
        return self._get_operation("code")

    def shell(self):
        return self._get_operation("shell")

    def _get_operation(self, name):
        """get operation"""
        if name in self._instances:
            return self._instances[name]
        operation_info = OperationRegistry.get_operation_info(name, self.mode)
        # Lazy loading: try to import the module if not registered
        if not operation_info:
            try:
                import importlib
                module_path = f"openjiuwen.core.sys_operation.{self.mode.value}.{name}_operation"
                importlib.import_module(module_path)
                # Check again after import
                operation_info = OperationRegistry.get_operation_info(name, self.mode)
            except (ImportError, AttributeError):
                pass
        if operation_info is None:
            return None
        operation_cls = operation_info["cls"]
        operation_desc = operation_info["description"]
        instance = operation_cls(name, self.mode, operation_desc, self._run_config)
        self._instances[name] = instance
        return instance
