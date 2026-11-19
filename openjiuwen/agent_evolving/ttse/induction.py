# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary flat-path shim for ``ttse.induction``.

Canonical location: :mod:`openjiuwen.agent_evolving.ttse.evolve.induction`.
Delete after callers no longer import this path.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.ttse.evolve.induction import *  # noqa: F403
from openjiuwen.agent_evolving.ttse.evolve import induction as _impl

__all__ = list(_impl.__all__)
