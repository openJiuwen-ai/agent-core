# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from typing import List

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.rails.device_lifecycle_rail import (
    DeviceLifecycleRail,
)
from openjiuwen.harness.tools.mobile_gui.rails.goal_anchor_injector_rail import (
    GoalAnchorInjectorRail,
)
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_context_summarizer_rail import (
    MultimodalContextSummarizerRail,
)
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_branch_rail import (
    MultimodalSkillBranchRail,
)
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
    MultimodalSkillReadRail,
)
from openjiuwen.harness.tools.mobile_gui.rails.vlm_grounding_perception_rail import (
    VlmGroundingPerceptionRail,
)
from openjiuwen.harness.workspace.workspace import Workspace


def resolve_mobile_skill_root(workspace: Workspace | str | Path | None) -> str:
    if workspace is None:
        return str(Path.cwd() / "skills")
    if isinstance(workspace, (str, Path)):
        return str(Path(workspace) / "skills")
    node = workspace.get_node_path("skills")
    if node is not None:
        return str(node)
    return str(Path(workspace.root_path) / "skills")


def infer_model_display_name(model: Model | None) -> str:
    if model is None:
        return ""
    mc = getattr(model, "model_config", None)
    if mc is None:
        return ""
    name = getattr(mc, "model", None) or getattr(mc, "model_name", None)
    return str(name or "")


def build_mobile_gui_rails(
    settings: MobileGuiRuntimeSettings,
    *,
    skill_root: str,
    model: Model | None,
) -> List[AgentRail]:
    model_name = infer_model_display_name(model)
    return [
        DeviceLifecycleRail(settings),
        VlmGroundingPerceptionRail(settings, model_name=model_name),
        MultimodalContextSummarizerRail(settings.mcs_screenshots_to_keep),
        GoalAnchorInjectorRail(),
        MultimodalSkillBranchRail(settings),
        MultimodalSkillReadRail(
            skill_root,
            skill_consult_mode=settings.skill_consult_mode,
        ),
    ]
