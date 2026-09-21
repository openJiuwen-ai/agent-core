"""Pydantic contracts shared by the embedded PersonalContext core."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PersonalContextState = Literal["CREATED", "CONFIGURED", "STARTING", "RUNNING", "STOPPING", "STOPPED", "FAILED"]
_FetchState = Literal["STOPPED", "STARTING", "RUNNING", "STOPPING", "FAILED"]
_FETCH_RUN_STATES = {
    "idle",
    "running",
    "stopping",
    "succeeded",
    "partial_succeeded",
    "failed",
    "cancelled",
}
_FETCH_RUN_PROGRESS_FIELDS = {
    "service_id",
    "run_state",
    "progress_percent",
    "total_items",
    "completed_items",
    "failed_items",
    "quarantined_items",
    "item_errors",
    "omitted_item_errors",
    "last_error",
}


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
    collection_enabled: bool
    agent_use_enabled: bool
    state: _PersonalContextState
    pipeline_running: bool
    pipeline_queue_size: int = Field(ge=0)
    fetch_service_states: dict[str, _FetchState]
    fetch_service_errors: dict[str, str]
    fetch_run_progress: dict[str, dict[str, object]] = Field(default_factory=dict)
    context_root: str = Field(min_length=1)
    context_ready: bool
    last_error: dict[str, object] | None = None

    @model_validator(mode="before")
    @classmethod
    def copy_nested_state(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        copied = deepcopy(dict(value))
        for field_name in (
            "fetch_service_states",
            "fetch_service_errors",
            "fetch_run_progress",
            "last_error",
        ):
            if copied.get(field_name) is not None:
                copied[field_name] = _copy_mapping(copied[field_name], field_name=field_name)
        for progress in copied.get("fetch_run_progress", {}).values():
            if isinstance(progress, dict):
                progress.setdefault("failed_items", 0)
                progress.setdefault("quarantined_items", 0)
                progress.setdefault("item_errors", [])
                progress.setdefault("omitted_item_errors", 0)
        return copied

    @field_validator("fetch_service_errors")
    @classmethod
    def bound_error_messages(cls, value: dict[str, str]) -> dict[str, str]:
        if any(len(message) > 512 for message in value.values()):
            raise ValueError("fetch service errors must be at most 512 characters")
        return value

    @field_validator("fetch_run_progress")
    @classmethod
    def validate_fetch_run_progress(
        cls,
        value: dict[str, dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        for service_id, progress in value.items():
            if not service_id or set(progress) != _FETCH_RUN_PROGRESS_FIELDS:
                raise ValueError("fetch run progress has an invalid shape")
            if progress["service_id"] != service_id:
                raise ValueError("fetch run progress service_id does not match its key")
            run_state = progress["run_state"]
            if run_state not in _FETCH_RUN_STATES:
                raise ValueError("fetch run progress has an invalid run_state")
            numeric: dict[str, int] = {}
            for field_name in (
                "progress_percent",
                "total_items",
                "completed_items",
                "failed_items",
                "quarantined_items",
                "omitted_item_errors",
            ):
                field_value = progress[field_name]
                if isinstance(field_value, bool) or not isinstance(field_value, int):
                    raise ValueError(f"fetch run progress {field_name} must be an integer")
                numeric[field_name] = field_value
            percent = numeric["progress_percent"]
            total = numeric["total_items"]
            completed = numeric["completed_items"]
            failed = numeric["failed_items"]
            quarantined = numeric["quarantined_items"]
            omitted = numeric["omitted_item_errors"]
            if (
                not 0 <= percent <= 100
                or total < 0
                or completed < 0
                or failed < 0
                or completed + failed > total
                or quarantined < 0
                or omitted < 0
            ):
                raise ValueError("fetch run progress counts are out of range")
            item_errors = progress["item_errors"]
            if not isinstance(item_errors, (list, tuple)) or len(item_errors) > 20:
                raise ValueError("fetch run progress item_errors must be a bounded list")
            for item_error in item_errors:
                if not isinstance(item_error, Mapping) or set(item_error) != {
                    "item_ref",
                    "code",
                    "message",
                    "failed_at",
                }:
                    raise ValueError("fetch run progress item_error has an invalid shape")
                code = item_error["code"]
                if isinstance(code, bool) or not isinstance(code, int):
                    raise ValueError("fetch run progress item_error code must be an integer")
                for field_name in ("item_ref", "message"):
                    field_value = item_error[field_name]
                    if not isinstance(field_value, str) or not field_value.strip() or len(field_value) > 256:
                        raise ValueError(
                            f"fetch run progress item_error {field_name} must be a bounded non-empty string"
                        )
                failed_at = item_error["failed_at"]
                if not isinstance(failed_at, str) or len(failed_at) > 64:
                    raise ValueError("fetch run progress item_error failed_at must be RFC 3339")
                try:
                    parsed_failed_at = datetime.fromisoformat(failed_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError("fetch run progress item_error failed_at must be RFC 3339") from exc
                if parsed_failed_at.tzinfo is None:
                    raise ValueError("fetch run progress item_error failed_at must include a timezone")
            if len(item_errors) + omitted != quarantined:
                raise ValueError("fetch run progress quarantined diagnostics are inconsistent")
            if run_state == "succeeded":
                if completed != total or failed != 0:
                    raise ValueError("succeeded fetch run progress requires all items completed")
                if percent != 100:
                    raise ValueError("succeeded fetch run progress must be 100 percent")
            elif run_state == "partial_succeeded":
                if completed == 0 or failed == 0 or completed + failed != total:
                    raise ValueError("partial fetch run progress requires completed and failed items")
                if percent != 100:
                    raise ValueError("partial fetch run progress must be 100 percent")
            if run_state == "idle":
                has_progress = percent != 0 or total != 0 or completed != 0 or failed != 0
                if has_progress:
                    raise ValueError("idle fetch run progress must be empty")
            last_error = progress["last_error"]
            if run_state == "failed":
                all_items_failed = total > 0 and completed == 0 and failed == total and percent == 100
                if all_items_failed:
                    if last_error is not None:
                        raise ValueError("item failure progress must not contain last_error")
                elif not isinstance(last_error, str) or not last_error.strip() or len(last_error) > 512:
                    raise ValueError("system failure progress requires a bounded last_error")
            elif last_error is not None:
                raise ValueError("only failed fetch run progress may contain last_error")
            if percent == 100 and run_state not in {"succeeded", "partial_succeeded", "failed"}:
                raise ValueError("only terminal fetch run progress may be 100 percent")
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
    operation: Literal["upsert"]
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
        if self.content is None or not self.content.strip():
            raise ValueError("upsert changes require non-empty content")
        return self


class FetchBatch(BaseModel):
    """One bounded batch emitted by a provider during a fetch run."""

    model_config = ConfigDict(extra="forbid", frozen=True, ser_json_bytes="base64")

    batch_id: str = Field(min_length=1)
    items: tuple[RawChangeItem, ...] = Field(default_factory=tuple, max_length=20)
    attempted_count: int = Field(default=0, ge=0, le=20, strict=True)
    success_offsets: tuple[int, ...] = ()
    skipped_offsets: tuple[int, ...] = ()
    failures: tuple[dict[str, object], ...] = ()
    next_cursor: dict[str, object] | None = None
    materialized_source_path: str | None = None
    materialized_revision: str | None = None

    @model_validator(mode="before")
    @classmethod
    def copy_input(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        copied = deepcopy(dict(value))
        outcome_fields = {"attempted_count", "success_offsets", "skipped_offsets", "failures"}
        if not outcome_fields.intersection(copied):
            item_count = len(copied.get("items") or ())
            copied.update(
                attempted_count=item_count,
                success_offsets=tuple(range(item_count)),
                skipped_offsets=(),
                failures=(),
            )
        if copied.get("next_cursor") is not None:
            copied["next_cursor"] = _copy_mapping(copied["next_cursor"], field_name="next_cursor")
        if copied.get("failures") is not None:
            raw_failures = copied["failures"]
            if isinstance(raw_failures, (str, bytes)) or not isinstance(raw_failures, (list, tuple)):
                raise ValueError("failures must be a list or tuple")
            copied["failures"] = tuple(
                _copy_mapping(failure, field_name="failure") for failure in raw_failures
            )
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
        for field_name, offsets in (
            ("success_offsets", self.success_offsets),
            ("skipped_offsets", self.skipped_offsets),
        ):
            if any(isinstance(offset, bool) or not isinstance(offset, int) for offset in offsets):
                raise ValueError(f"{field_name} must contain integers")
            if tuple(sorted(set(offsets))) != offsets:
                raise ValueError(f"{field_name} must be strictly increasing")
        failure_offsets: list[int] = []
        for failure in self.failures:
            if set(failure) != {"offset", "item_ref", "code", "message"}:
                raise ValueError("failure must contain offset, item_ref, code, and message")
            offset = failure["offset"]
            code = failure["code"]
            item_ref = failure["item_ref"]
            message = failure["message"]
            if isinstance(offset, bool) or not isinstance(offset, int):
                raise ValueError("failure offset must be an integer")
            if isinstance(code, bool) or not isinstance(code, int):
                raise ValueError("failure code must be an integer")
            if not isinstance(item_ref, str) or not item_ref.strip() or len(item_ref) > 256:
                raise ValueError("failure item_ref must be a bounded non-empty string")
            if not isinstance(message, str) or not message.strip() or len(message) > 256:
                raise ValueError("failure message must be a bounded non-empty string")
            failure_offsets.append(offset)
        if len(set(failure_offsets)) != len(failure_offsets):
            raise ValueError("failure offsets must be unique")
        if len(self.items) != len(self.success_offsets):
            raise ValueError("items must match success_offsets")
        success = set(self.success_offsets)
        skipped = set(self.skipped_offsets)
        failed = set(failure_offsets)
        if success & skipped or success & failed or skipped & failed:
            raise ValueError("candidate outcome offsets must be disjoint")
        if success | skipped | failed != set(range(self.attempted_count)):
            raise ValueError("candidate outcomes must cover attempted_count")
        return self
