# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary flat-path shim for ``ttse.index``.

Canonical location: :mod:`openjiuwen.agent_evolving.ttse.bank.index`.
Delete after callers no longer import this path.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.ttse.bank.index import *  # noqa: F403
from openjiuwen.agent_evolving.ttse.bank import index as _impl

__all__ = list(_impl.__all__)
