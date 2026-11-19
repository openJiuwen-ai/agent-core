# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary flat-path shim for ``ttse.success``.

Canonical location: :mod:`openjiuwen.agent_evolving.ttse.evolve.success`.
Delete after callers no longer import this path.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.ttse.evolve.success import *  # noqa: F403
from openjiuwen.agent_evolving.ttse.evolve import success as _impl

__all__ = list(_impl.__all__)
