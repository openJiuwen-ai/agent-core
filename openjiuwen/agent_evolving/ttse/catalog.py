# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary flat-path shim for ``ttse.catalog``.

Canonical location: :mod:`openjiuwen.agent_evolving.ttse.bank.catalog`.
Delete after jiuwenswarm no longer imports this path.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.ttse.bank.catalog import *  # noqa: F403
from openjiuwen.agent_evolving.ttse.bank import catalog as _impl

__all__ = list(_impl.__all__)
