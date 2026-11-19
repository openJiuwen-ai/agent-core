# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary flat-path shim for ``ttse.induce_context``.

Canonical location: :mod:`openjiuwen.agent_evolving.ttse.evidence.induce_context`.
Delete after callers no longer import this path.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.ttse.evidence.induce_context import *  # noqa: F403
from openjiuwen.agent_evolving.ttse.evidence import induce_context as _impl

__all__ = list(_impl.__all__)
