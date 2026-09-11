# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Versioned ``fingerprint.json`` artifact storage."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.symphony.models import FingerprintArtifact
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json, read_json
from openjiuwen.symphony.shared.fingerprint.settings import FINGERPRINT_SCHEMA_VERSION

FINGERPRINT_ARTIFACT_FILENAME = "fingerprint.json"


class FingerprintArtifactStore:
    """Read and atomically publish the canonical fingerprint artifact."""

    def __init__(self, artifact_root: str | Path) -> None:
        self.root = Path(artifact_root).expanduser().resolve()
        self.path = self.root / FINGERPRINT_ARTIFACT_FILENAME

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> FingerprintArtifact:
        if not self.path.is_file():
            raise build_error(
                StatusCode.COMPONENT_SYMPHONY_ARTIFACT_NOT_FOUND,
                reason=f"{FINGERPRINT_ARTIFACT_FILENAME} does not exist",
            )
        try:
            payload = read_json(self.path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_ARTIFACT_READ_CALL_FAILED,
                cause=exc,
                reason=type(exc).__name__,
            )
        _validate_schema_version(payload.get("schema_version"))
        try:
            return FingerprintArtifact.model_validate(payload)
        except PydanticValidationError as exc:
            raise build_error(
                StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                cause=exc,
                reason="fingerprint artifact does not match its declared schema",
            ) from exc

    def publish(self, artifact: FingerprintArtifact) -> None:
        try:
            validated = FingerprintArtifact.model_validate(artifact)
        except (PydanticValidationError, TypeError) as exc:
            raise build_error(
                StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                cause=exc,
                reason="fingerprint artifact is invalid before publication",
            ) from exc
        _validate_schema_version(validated.schema_version)
        try:
            atomic_write_json(self.path, validated.model_dump(mode="json"))
        except (OSError, TypeError, ValueError) as exc:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_ARTIFACT_WRITE_CALL_FAILED,
                cause=exc,
                reason=type(exc).__name__,
            )


def _validate_schema_version(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise build_error(
            StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
            reason="schema_version is required",
        )
    supported_major = FINGERPRINT_SCHEMA_VERSION.split(".", maxsplit=1)[0]
    artifact_major = value.split(".", maxsplit=1)[0]
    if artifact_major != supported_major:
        raise_error(
            StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
            reason=f"unsupported fingerprint schema major version: {artifact_major}",
        )


__all__ = ["FINGERPRINT_ARTIFACT_FILENAME", "FingerprintArtifactStore"]
