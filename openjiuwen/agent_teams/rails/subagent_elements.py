# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Manifest declarations for openjiuwen's built-in sub-agent providers.

Registers the generic helper sub-agents (explore / plan / browser) as
``SUBAGENT`` providers resolved via ``SubAgentSpec.factory_name`` at build time.
Each delegates to the matching ``build_*_agent_config`` helper, sourcing the
parent model from ``context.extras["_parent_model"]`` (published by
``DeepAgentSpec.build``) and the workspace root from ``context.workspace``.
``language`` and ``max_iterations`` are serializable params the caller bakes into
``SubAgentSpec.factory_kwargs`` (so any per-platform language policy is applied
upstream, keeping these providers generic).

The code sub-agent intentionally stays platform-side: it reuses the platform's
own coding-memory rail, which cannot be modeled generically here.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ConstructionInput,
    ElementKind,
    context_field,
    harness_element,
    param_field,
)
from openjiuwen.harness.subagents.browser_agent import build_browser_agent_config
from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config
from openjiuwen.harness.subagents.plan_agent import build_plan_agent_config

logger = logging.getLogger(__name__)

EXPLORE_AGENT = "core.explore_agent"
PLAN_AGENT = "core.plan_agent"
BROWSER_AGENT = "core.browser_agent"

# Key under ``context.extras`` where ``DeepAgentSpec.build`` publishes the
# resolved parent model for sub-agent providers to reuse.
_PARENT_MODEL_EXTRAS_KEY = "_parent_model"
_DEFAULT_MAX_ITERATIONS = 15


def _workspace_root(context: Any) -> str:
    """Resolve the member workspace root path (defaults to ``./``)."""
    workspace = getattr(context, "workspace", None)
    return str(getattr(workspace, "root_path", None) or "./")


def _parent_model(context: Any) -> Any:
    """Return the parent model published on the build context (or None)."""
    extras = getattr(context, "extras", None) or {}
    return extras.get(_PARENT_MODEL_EXTRAS_KEY)


class SubAgentInput(ConstructionInput):
    """Construction inputs shared by the built-in sub-agents."""

    max_iterations: int = param_field(
        default=_DEFAULT_MAX_ITERATIONS,
        description="Maximum task-loop iterations for the sub-agent.",
    )
    language: str = param_field(
        default="en",
        description="Runtime-prompt language for the sub-agent.",
    )
    workspace_root: str = context_field(
        resolver=_workspace_root,
        default="./",
        description="Member workspace root (defaults to ./ when absent).",
    )


def _common_kwargs(inp: SubAgentInput) -> dict[str, Any]:
    """Build the shared ``build_*_agent_config`` kwargs from resolved inputs."""
    return {
        "workspace": inp.workspace_root,
        "language": inp.language,
        "max_iterations": inp.max_iterations,
    }


@harness_element(
    kind=ElementKind.SUBAGENT,
    name=EXPLORE_AGENT,
    description="Read-only exploration sub-agent (model inherited from the parent when present).",
    input_model=SubAgentInput,
)
def build_explore_agent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build the explore sub-agent config (parent model reused when present)."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    spec = build_explore_agent_config(model=_parent_model(context), **_common_kwargs(inp))
    spec.factory_kwargs = {"auto_create_workspace": False}
    return spec


@harness_element(
    kind=ElementKind.SUBAGENT,
    name=PLAN_AGENT,
    description="Planning sub-agent (model inherited from the parent when present).",
    input_model=SubAgentInput,
)
def build_plan_agent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build the plan sub-agent config (parent model reused when present)."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    spec = build_plan_agent_config(model=_parent_model(context), **_common_kwargs(inp))
    spec.factory_kwargs = {"auto_create_workspace": False}
    return spec


@harness_element(
    kind=ElementKind.SUBAGENT,
    name=BROWSER_AGENT,
    description="Browser automation sub-agent (requires a model; defaults BROWSER_DRIVER=managed).",
    input_model=SubAgentInput,
)
def build_browser_agent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build the browser sub-agent config (model required; skipped when absent)."""
    inp = SubAgentInput.resolve(factory_kwargs, context)
    model = _parent_model(context)
    if model is None:
        logger.warning("[browser_agent] skipped: no parent model on build context")
        return None
    if not str(os.getenv("BROWSER_DRIVER") or "").strip():
        os.environ["BROWSER_DRIVER"] = "managed"
    spec = build_browser_agent_config(model, **_common_kwargs(inp))
    spec.factory_kwargs = {"auto_create_workspace": False}
    return spec


__all__ = [
    "EXPLORE_AGENT",
    "PLAN_AGENT",
    "BROWSER_AGENT",
]
