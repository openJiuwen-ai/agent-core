# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary flat-path shim for ``ttse.tip_parse``.

Canonical location: :mod:`openjiuwen.agent_evolving.ttse.dream.tip_parse`.
Delete after callers no longer import this path.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.ttse.dream.tip_parse import *  # noqa: F403
from openjiuwen.agent_evolving.ttse.dream import tip_parse as _impl

__all__ = list(_impl.__all__)
