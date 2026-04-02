import os
import re
from pathlib import Path
from typing import Dict, List

from openjiuwen.core.sys_operation.sys_operation import SysOperation


class DirectoryBuilder:
    """Build the directory structure using SysOperation"""

    def __init__(self, sys_operation: SysOperation, root_path: str = ""):
        self.sys_operation = sys_operation
        self.root_path = root_path

    async def build(self, directories: List[Dict]) -> None:
        """Build the actual directory structure based on the directory metadata."""
        for node in directories:
            await self._create_directory_recursive(node)

    @staticmethod
    def _is_safe_path(path: str) -> bool:
        if path is None or path == "":
            return True

        if os.path.isabs(path):
            return False
        if path.startswith(("/", "\\")):
            return False

        if re.match(r"^[A-Za-z]:", path):
            return False
        if path.startswith("\\\\"):
            return False

        p = path.replace("\\", "/")
        normalized = os.path.normpath(p).replace("\\", "/")
        parts = [x for x in normalized.split("/") if x not in ("", ".")]
        if ".." in parts:
            return False

        return True

    async def _create_directory_recursive(self, node: Dict, parent_path: str = "") -> None:
        """Create directories recursively."""
        relative_path = node.get("path", "")

        if not self._is_safe_path(relative_path):
            raise ValueError(f"Unsafe path detected: {relative_path}")

        if parent_path:
            full_path = f"{parent_path}/{relative_path}"
        else:
            full_path = f"{self.root_path}/{relative_path}" if self.root_path else relative_path

        is_file = node.get("is_file", False)
        default_content = node.get("default_content", "")
        if is_file:
            await self.sys_operation.fs().write_file(
                full_path,
                content=default_content,
                create_if_not_exist=True
            )
        else:
            marker_file = f"{full_path}/.workspace"
            await self.sys_operation.fs().write_file(
                marker_file,
                content="",
                create_if_not_exist=True
            )
        for child in node.get("children", []):
            await self._create_directory_recursive(child, full_path)
