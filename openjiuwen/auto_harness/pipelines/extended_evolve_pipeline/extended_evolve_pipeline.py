# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Placeholder extended evolve pipeline."""

from __future__ import annotations

from typing import Any, AsyncIterator

from openjiuwen.auto_harness.contexts import (
    SessionContext,
)
from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.pipelines.base import (
    BasePipeline,
)


class ExtendedEvolvePipeline(BasePipeline):
    """Placeholder until the extended pipeline is implemented."""

    name = EXTENDED_EVOLVE_PIPELINE
    description = "Extended evolve generation pipeline."
    expected_outputs = ["package_result"]

    async def stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        yield ctx.message(
            "当前已选择扩展流水线，但实现尚未完成: "
            f"{EXTENDED_EVOLVE_PIPELINE}"
        )
