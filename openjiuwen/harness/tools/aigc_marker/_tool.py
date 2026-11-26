# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""LocalFunction factory with host-bound workspace and provenance."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import Timeout as FileLockTimeout

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard
from openjiuwen.core.sys_operation.local._rw_lock_manager import ReadWriteLockManager
from openjiuwen.harness.tools.aigc_marker._engine import file_digest, mark_file
from openjiuwen.harness.tools.aigc_marker._formats import FORMAT_HANDLERS
from openjiuwen.harness.tools.base_tool import ToolOutput


@dataclass(frozen=True)
class MarkerProvenance:
    """Service identities assigned by the host for newly marked files."""

    content_producer: str
    content_propagator: str
    id_prefix: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.content_producer, self.content_propagator, self.id_prefix)):
            raise ValueError("AIGC provenance requires a producer, propagator and ID prefix")

    def build_signature(self, digest: str) -> str:
        content_id = hashlib.sha256(digest.encode("ascii")).hexdigest()[:16]
        record_id = f"{self.id_prefix}-{content_id}"
        return json.dumps(
            {
                "Label": "1",
                "ContentProducer": self.content_producer,
                "ProduceID": record_id,
                "ReservedCode1": "",
                "ContentPropagator": self.content_propagator,
                "PropagateID": record_id,
                "ReservedCode2": "",
            },
            ensure_ascii=False,
        )


def _resolve_file(workspace: Path, file_path: str) -> Path:
    path = Path(file_path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("file_path must be an absolute path inside the tool workspace")
    path = path.resolve(strict=True)
    if not path.is_relative_to(workspace) or not path.is_file():
        raise ValueError("file_path must point to an existing file inside the tool workspace")
    if path.suffix.lower() not in FORMAT_HANDLERS:
        raise ValueError(f"Unsupported file extension: {path.suffix}")
    return path


def _mark(path: Path, provenance: MarkerProvenance, skip_visible: bool) -> dict[str, Any]:
    digest = file_digest(path)
    changed = mark_file(path, provenance.build_signature(digest), not skip_visible, digest)
    return {
        "file_path": str(path),
        "changed": changed,
        "requested_mode": "implicit" if skip_visible else "explicit_and_implicit",
    }


def _verify(path: Path) -> ToolOutput:
    from openjiuwen.harness.tools.aigc_marker._text_metadata import inspect_text_metadata

    result = inspect_text_metadata(path)
    if not result["guide_storage_verified"]:
        return ToolOutput(success=False, data=result, error="Metadata is outside the text guide's supported storage")
    return ToolOutput(success=True, data=result)


def _tool_card(name: str, description: str, agent_id: str | None, *, mutates: bool) -> ToolCard:
    properties: dict[str, Any] = {
        "file_path": {"type": "string", "description": "Absolute path of an existing file in the managed workspace."}
    }
    if mutates:
        properties["skip_visible"] = {
            "type": "boolean",
            "description": "Write only implicit metadata. Default writes both visible and implicit marks.",
            "default": False,
        }
    return ToolCard(
        id=f"{name}_{agent_id}" if agent_id else name,
        name=name,
        description=description,
        input_params={"type": "object", "properties": properties, "required": ["file_path"]},
        idempotent=not mutates,
        parallel_safe=not mutates,
    )


def create_aigc_marker_tools(
    workspace: str | Path,
    *,
    provenance: MarkerProvenance,
    agent_id: str | None = None,
) -> list[Tool]:
    """Create two opt-in tools for a host-managed local workspace."""
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir() or root == root.parent:
        raise ValueError("AIGC marker requires a dedicated local workspace directory")

    async def mark(file_path: str, skip_visible: bool = False) -> ToolOutput:
        try:
            path = _resolve_file(root, file_path)
            async with ReadWriteLockManager.lock_guard(path, "write", 300):
                worker = asyncio.create_task(asyncio.to_thread(_mark, path, provenance, skip_visible))
                try:
                    return ToolOutput(success=True, data=await asyncio.shield(worker))
                except asyncio.CancelledError:
                    # Keep the write lock until the worker has finished replacing its staged file.
                    await asyncio.gather(worker, return_exceptions=True)
                    raise
        except ModuleNotFoundError as exc:
            return ToolOutput(success=False, error=f"AIGC marker dependency is missing: {exc.name}")
        except FileLockTimeout:
            return ToolOutput(success=False, error="Timed out waiting for access to the target file")
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    async def verify(file_path: str) -> ToolOutput:
        try:
            path = _resolve_file(root, file_path)
            return await asyncio.to_thread(_verify, path)
        except ModuleNotFoundError as exc:
            return ToolOutput(success=False, error=f"AIGC marker dependency is missing: {exc.name}")
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    return [
        LocalFunction(
            _tool_card(
                "aigc_mark_file",
                "Add AIGC marks to an existing document, diagram, image, audio or video file in place.",
                agent_id,
                mutates=True,
            ),
            mark,
        ),
        LocalFunction(
            _tool_card(
                "aigc_verify_file",
                "Read and verify implicit AIGC metadata in text formats covered by TC260-PG-20258A.",
                agent_id,
                mutates=False,
            ),
            verify,
        ),
    ]
