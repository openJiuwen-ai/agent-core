# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared credential redaction for MoA verification inputs."""

import re


def redact(value: str) -> str:
    """Remove common credential forms from problem and step summaries."""
    # Preserve JSON syntax while removing complete quoted secret values,
    # including escaped characters, from tool arguments and result text.
    value = re.sub(
        r'(?i)("(?:api[_-]?key|token|password)"\s*:\s*)"(?:\\.|[^"\\])*"',
        r'\1"[redacted]"',
        value,
    )
    return re.sub(
        r"(?i)(bearer\s+|(?:api[_-]?key|token|password)\s*[:=]\s*|sk-)[A-Za-z0-9_./+=-]+",
        "[redacted]",
        value,
    )
