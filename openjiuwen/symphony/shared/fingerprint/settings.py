# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configuration for capability fingerprint construction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

FINGERPRINT_SCHEMA_VERSION = "1.0"
EXTRACTION_PROTOCOL_VERSION = "symphony-capability-extraction-v2"
EVALUATION_PROTOCOL_VERSION = "symphony-capability-evaluation-v1"


@dataclass(frozen=True)
class FingerprintSettings:
    """Explicit, application-independent fingerprint build settings.

    LLM-backed extraction and evaluation are deliberately opt-in because they
    incur extra model calls. Deterministic metadata extraction and static
    evaluation are always available.
    """

    enable_llm_extraction: bool = False
    enable_llm_evaluation: bool = False
    batch_size: int = 16
    max_concurrency: int = 4
    body_limit: int = 16_000
    llm_max_tokens: int = 2_048
    llm_timeout: float | None = 60.0
    evidence_text_limit: int = 512
    cache_enabled: bool = True
    extraction_protocol_version: str = EXTRACTION_PROTOCOL_VERSION
    evaluation_protocol_version: str = EVALUATION_PROTOCOL_VERSION
    configuration_signature: str = ""

    def signature(self) -> str:
        """Return a deterministic signature for incremental cache validation."""

        payload = {
            "schema_version": FINGERPRINT_SCHEMA_VERSION,
            **asdict(self),
        }
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "EVALUATION_PROTOCOL_VERSION",
    "EXTRACTION_PROTOCOL_VERSION",
    "FINGERPRINT_SCHEMA_VERSION",
    "FingerprintSettings",
]
