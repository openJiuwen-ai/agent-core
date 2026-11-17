# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal fingerprint protocol identifiers; policy lives in symphony.config."""

from openjiuwen.symphony.config import FingerprintProtocolConfig

_protocol = FingerprintProtocolConfig()
FINGERPRINT_SCHEMA_VERSION = _protocol.schema_version
EXTRACTION_PROTOCOL_VERSION = _protocol.extraction_version
EVALUATION_PROTOCOL_VERSION = _protocol.evaluation_version
