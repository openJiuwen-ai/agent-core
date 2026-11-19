# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary flat-path shim for ``ttse.consult``.

Canonical location: :mod:`openjiuwen.agent_evolving.ttse.recall.consult`.
Delete after jiuwenswarm no longer imports this path.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.ttse.recall.consult import *  # noqa: F403
from openjiuwen.agent_evolving.ttse.recall import consult as _impl

__all__ = list(_impl.__all__)
