# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent backends: the pluggable execution layer.

Ships the engine-internal backends only. The team integration backend
(``TeamWorkerBackend``) lives in ``workflow/backends`` so the engine package
stays free of any agent_teams dependency.
"""
from __future__ import annotations

from .base import AgentBackend, AgentResult
from .mock import SKIP, MockBackend

__all__ = ["AgentBackend", "AgentResult", "MockBackend", "SKIP"]
