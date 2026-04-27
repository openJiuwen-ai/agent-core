# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context engineer rails for configuring context engine and system prompt injection."""
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail
from openjiuwen.harness.rails.context_engineer.context_assemble_rail import ContextAssembleRail

__all__ = [
    "ContextProcessorRail",
    "ContextAssembleRail",
]
