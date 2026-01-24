# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Dict, Any, Type

from openjiuwen.core.sys_operation.base import BaseOperation, OperationMode


class OperationRegistry:
    """Operation registry, managing the operation cls."""
    _repository: Dict[str, Dict[OperationMode, Dict[str, Any]]] = {}  # {name: {mode: {cls, description}}}

    @classmethod
    def register(cls, operation_cls: Type[BaseOperation],
                 name: str, mode: OperationMode, description: str):
        """
        Register operation cls to the repository.

        Args:
            operation_cls: Operation cls.
            name: Unique identifier for the operation, e.g., "file", "code", "shell".
            mode: Running mode associated with the operation, e.g., "local" or "sandbox".
            description: Human-readable description of the operation, e.g., "file operation, including read_file".
        """
        if name not in cls._repository:
            cls._repository[name] = {}
        cls._repository[name][mode] = {
            "cls": operation_cls,
            "description": description
        }

    @classmethod
    def get_operation_info(cls, name: str, mode: OperationMode) -> Dict[str, Any]:
        """
        Get the operation cls info by the name and mode.

        Args:
            name: Unique identifier for the operation, e.g., "file", "code", "shell".
            mode: Running mode associated with the operation, e.g., "local" or "sandbox".

        Returns:
            The operation cls info, including cls and description.
        """
        return cls._repository.get(name, {}).get(mode, {})


def operation(name: str, mode: OperationMode, description: str = ""):
    """Decorator for registering a class as an operation in the OperationRegistry.

    Args:
        name: Unique identifier for the operation.
        mode: Running mode associated with the operation.
        description: Human-readable description of the operation, default value is "".

    Returns:
        The original class.
    """

    def decorator(cls):
        OperationRegistry.register(cls, name, mode, description)
        return cls

    return decorator
