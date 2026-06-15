# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class QARef:
    """QA slice descriptor for qa_artifact layer (§5.7). Canonical definition."""

    qa_id: str
    tokens: int
    is_history: bool
    get_messages: Callable[[], list]
