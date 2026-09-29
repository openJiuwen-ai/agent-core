# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capability fingerprint scanning, extraction, evaluation, and artifact I/O."""

from openjiuwen.symphony.shared.fingerprint.artifact import (
    FINGERPRINT_ARTIFACT_FILENAME,
    FingerprintArtifactStore,
)
from openjiuwen.symphony.shared.fingerprint.extractor import (
    CapabilityFingerprintExtractor,
    ExtractionOutcome,
    capability_content_hash,
)
from openjiuwen.symphony.shared.fingerprint.graph_compat import (
    ArtifactSpec,
    CapabilityFingerprint,
    Fingerprint,
    FingerprintLike,
    ParameterSpec,
    coerce_fingerprint,
)
from openjiuwen.symphony.shared.fingerprint.normalization import (
    DataTypeResolution,
    DataTypeVocabulary,
    IONameResolution,
    IONameTerm,
    IONameVocabulary,
    IONormalizationResult,
    NormalizationDecision,
    NormalizationIssue,
    build_io_name_vocabulary,
    normalize_capability_type,
    normalize_io_name,
    normalize_io_specs,
    normalize_io_specs_with_audit,
    normalize_io_specs_with_issues,
)
from openjiuwen.symphony.shared.fingerprint.parser import (
    ManifestDiagnostic,
    ParsedSkillManifest,
    SkillManifestParser,
)
from openjiuwen.symphony.shared.fingerprint.scanner import (
    ScanDiagnostic,
    ScanResult,
    SkillFolderScanner,
    build_source_snapshot,
)
from openjiuwen.symphony.shared.fingerprint.service import FingerprintService
from openjiuwen.symphony.shared.fingerprint.settings import (
    EVALUATION_PROTOCOL_VERSION,
    EXTRACTION_PROTOCOL_VERSION,
    FINGERPRINT_SCHEMA_VERSION,
    FingerprintSettings,
)

__all__ = [
    "EVALUATION_PROTOCOL_VERSION",
    "EXTRACTION_PROTOCOL_VERSION",
    "FINGERPRINT_ARTIFACT_FILENAME",
    "FINGERPRINT_SCHEMA_VERSION",
    "ArtifactSpec",
    "CapabilityFingerprint",
    "CapabilityFingerprintExtractor",
    "DataTypeResolution",
    "DataTypeVocabulary",
    "ExtractionOutcome",
    "FingerprintArtifactStore",
    "Fingerprint",
    "FingerprintLike",
    "FingerprintService",
    "FingerprintSettings",
    "IONameResolution",
    "IONameTerm",
    "IONameVocabulary",
    "IONormalizationResult",
    "ManifestDiagnostic",
    "NormalizationDecision",
    "NormalizationIssue",
    "ParameterSpec",
    "ParsedSkillManifest",
    "ScanDiagnostic",
    "ScanResult",
    "SkillFolderScanner",
    "SkillManifestParser",
    "build_io_name_vocabulary",
    "build_source_snapshot",
    "capability_content_hash",
    "coerce_fingerprint",
    "normalize_capability_type",
    "normalize_io_name",
    "normalize_io_specs",
    "normalize_io_specs_with_audit",
    "normalize_io_specs_with_issues",
]
