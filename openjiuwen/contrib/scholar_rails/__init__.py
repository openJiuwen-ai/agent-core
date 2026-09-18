# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Scholar rails —— openJiuwen 科研场景 Rail 扩展集（CCF BDCI 2026 参赛贡献）。

本包把 JiuwenScholar 参赛系统对 Harness 的四项 Rail 扩展以**可上游合入**的
形式落在 openJiuwen 源码树内：

- :class:`LiteratureGroundingRail`   文献锚定（机制级反幻觉引用）
- :class:`ResourceMeterRail`         全链路资源计量（token/成本/时长，可追溯）
- :class:`ICLRReviewRail`            ICLR 评审门禁（内生化质量闭环）
- :class:`ConfidenceGatedSkillEvolutionRail`  技能自演进成功率滑窗+置信度双门控

四个 Rail 均为**纯新增、零侵入**：不修改 Harness 任何现有类，仅通过
Rail 生命周期钩子注入能力，完全遵循 ``Agent = Model + Harness`` 的扩展范式。
"""
from __future__ import annotations

from openjiuwen.contrib.scholar_rails.resource_meter_rail import (
    MeterEvent,
    ResourceMeterRail,
    load_events,
    summarize,
)
from openjiuwen.contrib.scholar_rails.literature_grounding_rail import (
    LiteratureGroundingRail,
)
from openjiuwen.contrib.scholar_rails.iclr_review_rail import (
    ICLRReviewRail,
    ReviewScore,
)
from openjiuwen.contrib.scholar_rails.confidence_gated_evolution_rail import (
    ConfidenceGatedSkillEvolutionRail,
    EvolutionGateLog,
    TaskOutcome,
)

__all__ = [
    "MeterEvent",
    "ResourceMeterRail",
    "load_events",
    "summarize",
    "LiteratureGroundingRail",
    "ICLRReviewRail",
    "ReviewScore",
    "ConfidenceGatedSkillEvolutionRail",
    "EvolutionGateLog",
    "TaskOutcome",
]
