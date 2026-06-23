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


class BrowserSubAgentInput(SubAgentInput):
    """Browser sub-agent inputs: per-teammate browser identity (port + profile).

    All fields default to empty/0 → legacy single shared-browser behavior. Set
    ``browser_key`` (and optionally an explicit port/profile) to give a teammate
    its own isolated managed browser. Distinct keys across teammates isolate
    browsers; a shared key shares one browser.
    """

    browser_key: str = param_field(
        default="",
        description="Per-teammate browser identity key. Distinct keys isolate browsers.",
    )
    browser_port: int = param_field(
        default=0,
        description="Explicit managed Chrome debug port. 0 auto-allocates a free port.",
    )
    browser_profile: str = param_field(
        default="",
        description="Managed browser profile name. Defaults to the browser key.",
    )
    browser_driver: str = param_field(
        default="",
        description="Driver mode: managed / remote / extension. Defaults to managed.",
    )
    browser_cdp_url: str = param_field(
        default="",
        description="Remote-mode CDP endpoint URL (implies driver=remote).",
    )


def _browser_instance_dict(inp: BrowserSubAgentInput) -> dict[str, Any] | None:
    """Build a serializable browser-instance dict, or None for legacy behavior.

    Returns None when no browser identity is configured (preserves the single
    shared-browser path). Otherwise returns a dict that survives the spawn wire
    payload and reconstructs into ``BrowserInstanceConfig`` in the child process.
    """
    if not any((inp.browser_key, inp.browser_port, inp.browser_profile, inp.browser_driver, inp.browser_cdp_url)):
        return None
    data: dict[str, Any] = {}
    if inp.browser_key:
        data["key"] = inp.browser_key
    if inp.browser_port:
        data["managed_port"] = inp.browser_port
    if inp.browser_profile:
        data["profile_name"] = inp.browser_profile
    if inp.browser_cdp_url:
        data["cdp_url"] = inp.browser_cdp_url
    data["driver_mode"] = inp.browser_driver or ("remote" if inp.browser_cdp_url else "managed")
    return data


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
    input_model=BrowserSubAgentInput,
)
def build_browser_agent(factory_kwargs: dict[str, Any], context: Any) -> Any:
    """Build the browser sub-agent config (model required; skipped when absent)."""
    inp = BrowserSubAgentInput.resolve(factory_kwargs, context)
    model = _parent_model(context)
    if model is None:
        logger.warning("[browser_agent] skipped: no parent model on build context")
        return None
    spec = build_browser_agent_config(model, **_common_kwargs(inp))
    instance_dict = _browser_instance_dict(inp)
    if instance_dict is None:
        # Legacy single shared-browser behavior: managed driver via global env.
        if not str(os.getenv("BROWSER_DRIVER") or "").strip():
            os.environ["BROWSER_DRIVER"] = "managed"
        spec.factory_kwargs = {"auto_create_workspace": False}
    else:
        # Per-teammate isolation: carry browser identity as serializable
        # factory_kwargs (driver mode set on the instance, not global env).
        spec.factory_kwargs = {"auto_create_workspace": False, "browser_instance": instance_dict}
    return spec


__all__ = [
    "EXPLORE_AGENT",
    "PLAN_AGENT",
    "BROWSER_AGENT",
]
