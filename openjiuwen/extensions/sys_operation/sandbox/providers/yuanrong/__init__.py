# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility imports for callers of the former YuanRong provider."""

from openjiuwen.extensions.sys_operation.sandbox.providers.adx import (
    AdxCodeProvider as YuanrongCodeProvider,
    AdxFSProvider as YuanrongFSProvider,
    AdxShellProvider as YuanrongShellProvider,
    build_adx_shared_scope_key as build_yuanrong_shared_scope_key,
    delete_adx_sandbox as delete_yuanrong_sandbox,
)

__all__ = [
    "YuanrongCodeProvider",
    "YuanrongFSProvider",
    "YuanrongShellProvider",
    "build_yuanrong_shared_scope_key",
    "delete_yuanrong_sandbox",
]
