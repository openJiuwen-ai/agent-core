# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Small helpers for online LoRA runtime metadata."""

from __future__ import annotations

from typing import Any


def lora_id(user_id: str, version: str) -> str:
    return f"{user_id}:{version}"


def build_lora_info(user_id: str, version: Any, *, default_policy: str) -> dict[str, Any]:
    version_name = str(getattr(version, "version", "") or "")
    return {
        "model_id": user_id,
        "lora_id": lora_id(user_id, version_name),
        "version": version_name,
        "path": str(getattr(version, "path", "") or ""),
        "base_model": str(getattr(version, "base_model", "") or ""),
        "parent_lora_id": str(getattr(version, "parent_lora_id", "") or ""),
        "parent_lora_version": str(getattr(version, "parent_lora_version", "") or ""),
        "availability_status": str(getattr(version, "availability_status", "pending") or "pending"),
        "training_source": str(getattr(version, "training_source", "base_model") or "base_model"),
        "default_policy": default_policy,
    }
