# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Sharing backends."""

from openjiuwen.agent_evolving.sharing.backends.base import SharingBackend
from openjiuwen.agent_evolving.sharing.backends.local_file import LocalFileBackend

__all__ = [
    "SharingBackend",
    "LocalFileBackend",
]
