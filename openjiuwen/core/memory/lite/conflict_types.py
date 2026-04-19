# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Memory conflict types and data models for coding memory."""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


class WriteMode(Enum):
    """Write operation mode."""

    CREATE = "create"  # Create new file
    APPEND = "append"  # Append to existing file
    SKIP = "skip"  # Skip (redundant)


@dataclass
class WriteResult:
    """Result of a memory write operation."""

    success: bool
    path: str
    mode: WriteMode  # "create", "append", or "skip"

    # Conflict info
    conflict_detected: bool = False
    conflicting_files: List[str] = field(default_factory=list)
    note: Optional[str] = None

    # Error info
    error: Optional[str] = None

    # Memory type (user, feedback, project, reference)
    type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for tool response."""
        result = {
            "success": self.success,
            "path": self.path,
            "mode": self.mode.value,
        }
        if self.type:
            result["type"] = self.type
        if self.conflict_detected:
            result["conflict_detected"] = True
            result["conflicting_files"] = self.conflicting_files
        if self.note:
            result["note"] = self.note
        if self.error:
            result["error"] = self.error
        return result
