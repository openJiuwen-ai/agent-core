# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from datetime import datetime

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, ValidationError
from openjiuwen.symphony.models import CapabilityFingerprint, CapabilityIO, FingerprintArtifact, SourceSnapshot
from openjiuwen.symphony.shared.fingerprint.artifact import FingerprintArtifactStore


def _artifact() -> FingerprintArtifact:
    return FingerprintArtifact(
        source_snapshot=SourceSnapshot(snapshot_id="snapshot-1", capability_count=1),
        fingerprints=(
            CapabilityFingerprint(
                capability_id="summarize",
                capability_type="skill",
                name="Summarize",
                description="Summarize supplied text.",
                content_hash="a" * 64,
            ),
        ),
    )


def test_fingerprint_artifact_round_trip_uses_singular_filename(tmp_path) -> None:
    store = FingerprintArtifactStore(tmp_path)

    store.publish(_artifact())

    assert store.path.name == "fingerprint.json"
    assert store.read() == _artifact().model_copy(update={"generated_at": store.read().generated_at})


def test_missing_artifact_is_a_non_retryable_validation_error(tmp_path) -> None:
    store = FingerprintArtifactStore(tmp_path)

    with pytest.raises(ValidationError) as exc_info:
        store.read()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_ARTIFACT_NOT_FOUND
    assert exc_info.value.recoverable is False


def test_reader_ignores_unknown_fields_in_same_major_version(tmp_path) -> None:
    store = FingerprintArtifactStore(tmp_path)
    payload = _artifact().model_dump(mode="json")
    payload["schema_version"] = "1.7"
    payload["future_envelope_field"] = {"enabled": True}
    payload["fingerprints"][0]["future_fingerprint_field"] = "ignored"
    tmp_path.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    artifact = store.read()

    assert artifact.schema_version == "1.7"
    assert artifact.fingerprints[0].capability_id == "summarize"


@pytest.mark.parametrize("schema_version", [None, "2.0"])
def test_reader_rejects_missing_or_unsupported_schema_version(tmp_path, schema_version) -> None:
    store = FingerprintArtifactStore(tmp_path)
    payload = _artifact().model_dump(mode="json")
    if schema_version is None:
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_version
    tmp_path.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BaseError) as exc_info:
        store.read()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID


def test_failed_atomic_publish_preserves_last_success(tmp_path, monkeypatch) -> None:
    store = FingerprintArtifactStore(tmp_path)
    original = _artifact()
    store.publish(original)

    def fail_replace(_source, _target) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("openjiuwen.symphony.shared.fingerprint._io.os.replace", fail_replace)

    with pytest.raises(BaseError) as exc_info:
        store.publish(
            original.model_copy(
                update={
                    "source_snapshot": SourceSnapshot(snapshot_id="snapshot-2", capability_count=1),
                }
            )
        )

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_ARTIFACT_WRITE_CALL_FAILED
    assert store.read().source_snapshot.snapshot_id == "snapshot-1"


def test_invalid_model_copy_cannot_replace_last_success(tmp_path) -> None:
    store = FingerprintArtifactStore(tmp_path)
    original = _artifact()
    store.publish(original)
    invalid = original.model_copy(
        update={"generated_at": datetime(2026, 8, 3, 8)},  # noqa: DTZ001 -- deliberate bypass attempt.
    )

    with pytest.raises(BaseError) as exc_info:
        store.publish(invalid)

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID
    assert store.read().source_snapshot.snapshot_id == "snapshot-1"


def test_nested_model_copy_is_revalidated_and_redacted_before_publish(tmp_path) -> None:
    store = FingerprintArtifactStore(tmp_path)
    original = _artifact()
    unsafe_io = CapabilityIO(name="query", type="text").model_copy(
        update={"name": "apiKey", "default": "publish-bypass-secret"},
    )
    unsafe_fingerprint = original.fingerprints[0].model_copy(update={"inputs": (unsafe_io,)})
    unsafe_artifact = original.model_copy(update={"fingerprints": (unsafe_fingerprint,)})

    store.publish(unsafe_artifact)

    raw = store.path.read_text(encoding="utf-8")
    assert "publish-bypass-secret" not in raw
    assert store.read().fingerprints[0].inputs[0].default is None


def test_reader_rejects_non_finite_json_numbers(tmp_path) -> None:
    store = FingerprintArtifactStore(tmp_path)
    payload = json.dumps(_artifact().model_dump(mode="json"))
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(payload[:-1] + ', "future_score": NaN}', encoding="utf-8")

    with pytest.raises(BaseError) as exc_info:
        store.read()

    assert exc_info.value.status is StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID
