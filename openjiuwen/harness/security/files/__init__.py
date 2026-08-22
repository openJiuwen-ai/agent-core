# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Prefer ``openjiuwen.harness.security.permission_engine.fileguard``."""

from openjiuwen.harness.security.permission_engine.fileguard.extract import extract_accesses_native

__all__ = ["extract_accesses_native"]
