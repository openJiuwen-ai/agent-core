"""Pydantic contracts shared by the embedded PersonalContext core."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PersonalContextState = Literal["CREATED", "CONFIGURED", "STARTING", "RUNNING", "STOPPING", "STOPPED", "FAILED"]
_FetchState = Literal["STOPPED", "STARTING", "RUNNING", "STOPPING", "FAILED"]


def _json_size(value: object, *, field_name: str, max_bytes: int | None = None) -> None:
    """Validate that a value is JSON encodable and optionally bounded."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc
    if max_bytes is not None and len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds {max_bytes} bytes")


def _copy_mapping(value: object, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    copied = deepcopy(dict(value))
    _json_size(copied, field_name=field_name)
    return copied


class PersonalContextStatus(BaseModel):
    """Bounded, credential-free snapshot of PersonalContext runtime state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    configured: bool
    enabled: bool
    fetching_enabled: bool
    state: _PersonalContextState
    pipeline_running: bool
    pipeline_queue_size: int = Field(ge=0)
    fetch_service_states: dict[str, _FetchState]
    fetch_service_errors: dict[str, str]
    context_root: str = Field(min_length=1)
    context_ready: bool
    last_error: dict[str, object] | None = None

    @model_validator(mode="before")
    @classmethod
    def copy_nested_state(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        copied = deepcopy(dict(value))
        for field_name in ("fetch_service_states", "fetch_service_errors", "last_error"):
            if copied.get(field_name) is not None:
                copied[field_name] = _copy_mapping(copied[field_name], field_name=field_name)
        return copied

    @field_validator("fetch_service_errors")
    @classmethod
    def bound_error_messages(cls, value: dict[str, str]) -> dict[str, str]:
        if any(len(message) > 512 for message in value.values()):
            raise ValueError("fetch service errors must be at most 512 characters")
        return value

    @field_validator("last_error")
    @classmethod
    def validate_last_error(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        if value is None:
            return None
        if set(value) != {"code", "status", "message", "operation"}:
            raise ValueError("last_error must contain only code, status, message, and operation")
        if isinstance(value["code"], bool) or not isinstance(value["code"], int):
            raise ValueError("last_error code must be an integer")
        for field_name in ("status", "operation"):
            field_value = value[field_name]
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"last_error {field_name} must be a non-empty string")
        message = value["message"]
        if not isinstance(message, str) or not message.strip():
            raise ValueError("last_error message must be a non-empty string")
        if len(message) > 512:
            raise ValueError("last_error message must be at most 512 characters")
        return value


class RawChangeItem(BaseModel):
    """One provider change handed to the Context pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True, ser_json_bytes="base64")

    logical_id: str = Field(min_length=1, max_length=512)
    revision_id: str = Field(min_length=1, max_length=256)
    operation: Literal["upsert", "delete"]
    title: str | None = None
    content: str | None = None
    original_ref: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)
    raw_snapshot: str | bytes | None = None

    @model_validator(mode="before")
    @classmethod
    def copy_input(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        copied = deepcopy(dict(value))
        if copied.get("metadata") is not None:
            copied["metadata"] = _copy_mapping(copied["metadata"], field_name="metadata")
        return copied

    @field_validator("logical_id", "revision_id", "original_ref")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must not be blank")
        return value

    @field_validator("content")
    @classmethod
    def bound_content(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 2_000_000:
            raise ValueError("content exceeds 2,000,000 characters")
        return value

    @field_validator("metadata")
    @classmethod
    def bound_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        _json_size(value, field_name="metadata", max_bytes=64 * 1024)
        return value

    @field_validator("raw_snapshot")
    @classmethod
    def bound_snapshot(cls, value: str | bytes | None) -> str | bytes | None:
        if value is None:
            return None
        size = len(value.encode("utf-8")) if isinstance(value, str) else len(value)
        if size > 2 * 1024 * 1024:
            raise ValueError("raw_snapshot exceeds 2 MiB")
        return value

    @model_validator(mode="after")
    def validate_operation_pairing(self) -> "RawChangeItem":
        if self.operation == "upsert" and (self.content is None or not self.content.strip()):
            raise ValueError("upsert changes require non-empty content")
        if self.operation == "delete" and (self.content is not None or self.raw_snapshot is not None):
            raise ValueError("delete changes must not carry content or raw_snapshot")
        return self


class FetchBatch(BaseModel):
    """One bounded batch emitted by a provider during a fetch run."""

    model_config = ConfigDict(extra="forbid", frozen=True, ser_json_bytes="base64")

    batch_id: str = Field(min_length=1)
    items: tuple[RawChangeItem, ...] = Field(default_factory=tuple, max_length=20)
    next_cursor: dict[str, object] | None = None
    materialized_source_path: str | None = None
    materialized_revision: str | None = None

    @model_validator(mode="before")
    @classmethod
    def copy_input(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        copied = deepcopy(dict(value))
        if copied.get("next_cursor") is not None:
            copied["next_cursor"] = _copy_mapping(copied["next_cursor"], field_name="next_cursor")
        return copied

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        if value in {".", ".."} or not _SAFE_SEGMENT.fullmatch(value):
            raise ValueError("batch_id must be a safe path segment")
        return value

    @field_validator("next_cursor")
    @classmethod
    def validate_cursor(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        if value is not None:
            _json_size(value, field_name="next_cursor")
        return value

    @field_validator("materialized_source_path")
    @classmethod
    def validate_materialized_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("materialized_source_path must be an absolute candidate path")
        resolved = path.resolve()
        if "candidate" not in {part.casefold() for part in resolved.parts}:
            raise ValueError("materialized_source_path must be an absolute candidate path")
        return str(resolved)

    @field_validator("materialized_revision")
    @classmethod
    def validate_materialized_revision(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("materialized_revision must not be blank")
        return value

    @model_validator(mode="after")
    def validate_materialized_pair(self) -> "FetchBatch":
        if (self.materialized_source_path is None) != (self.materialized_revision is None):
            raise ValueError("materialized source path and revision must be provided together")
        return self
