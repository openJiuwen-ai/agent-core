"""Internal execution strategy selection used by the public planning entrypoint."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.symphony.orchestration.contracts import PlanningInput
from openjiuwen.symphony.orchestration.model import (
    invoke_json, model_usage_context, thinking_disabled_request_overrides,
)
from openjiuwen.symphony.orchestration.language import planner_language_instruction
from openjiuwen.symphony.retrieval.search.runtime.lexical import LexicalDocument, LexicalIndex


class RoutingCapability(BaseModel):
    """A host-visible capability description, without executable instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: str = Field(min_length=1)
    name: str = ""
    description: str = ""


class PlanningContext(BaseModel):
    """Visible capabilities, required IDs, and host execution policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    request_id: str = ""
    context_complete: bool = True
    skills: list[RoutingCapability] = Field(default_factory=list)
    experts: list[RoutingCapability] = Field(default_factory=list)
    tools: list[RoutingCapability] = Field(default_factory=list)
    allow_experts: bool = True
    required_skill_ids: list[str] = Field(default_factory=list)
    required_expert_ids: list[str] = Field(default_factory=list)
    reconsiderations: int = Field(default=0, ge=0, le=1)


class _RouteDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    strategy: Literal[
        "direct_answer", "single_capability", "capability_workflow", "single_expert", "multi_expert",
    ]
    reason: str = Field(min_length=1, max_length=1000)
    skill_ids: list[str] = Field(default_factory=list)
    expert_ids: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)


class ExecutionRoute(_RouteDraft):
    """Validated internal decision; hosts consume its serialized result only."""

    requires_planning: bool
    request_id: str = ""


class RoutingUpgradeRequired(ValueError):
    """Planning found evidence requiring a fresh routing decision."""


_SYSTEM_PROMPT = """Choose one supplied strategy. Decide who owns the work before selecting tools.
Honor delegation limits and required capabilities. Copy IDs from catalog entries, never their descriptions:
skills → skill_ids, tools → tool_ids, experts → expert_ids. List each selected ID once.
direct_answer: Answer from known information; leave all ID lists empty.
single_capability: Use exactly one entry across skill_ids and tool_ids. A Skill can use tools internally.
capability_workflow: The main agent coordinates two or more Skills/tools for a batch of identical operations, alternate
formats of one result, or steps that must keep updating the same working result.
For these three strategies leave expert_ids empty. Host tools are used by the main agent.
File inputs are references for later reading. Treat truncated previews and partial catalogs as incomplete.
Flag unavailable required capabilities or genuinely missing material and keep a feasible route.
"""

_EXPERT_PROMPT = """
When delegation is allowed, choose by the work. An empty experts list permits both expert strategies:
use expert_ids=[] and the planner supplies temporary roles.
multi_expert: Choose this when two or more units can each deliver a verifiable finding, authored result,
or tested change without needing a sibling's output. This includes distinct evidence for one final decision.
The units may use identical methods, inputs or tools. A common final check or combined report joins them
afterwards. Also use this strategy for a necessary specialist handoff or an independent review.
single_expert: One specialist owns a coupled judgment that benefits from consistent domain expertise.
For expert routes, start with skill_ids=[] and tool_ids=[]; add only required host capabilities.
List a matching expert ID once even for several tasks. Use a temporary role for unmatched work.
In reason, name the independent results or the working result that couples them.
"""


async def inventory_context(provider: Any, expert_inventory: Any = None) -> PlanningContext:
    """Read configured SDK inventories without requiring or publishing a graph."""
    rows: dict[str, list[RoutingCapability]] = {"skill": [], "expert": []}
    for inventory in (provider, expert_inventory):
        if inventory is None:
            continue
        if callable(getattr(inventory, "scan", None)):
            values = (await asyncio.to_thread(inventory.scan, allow_partial=True)).capabilities
        elif callable(inventory):
            values = inventory()
            if inspect.isawaitable(values):
                values = await values
        elif callable(getattr(inventory, "capabilities", None)):
            values = inventory.capabilities()
            if inspect.isawaitable(values):
                values = await values
        elif isinstance(inventory, Sequence):
            values = inventory
        else:
            values = []
        for item in values:
            value = item if isinstance(item, Mapping) else vars(item)
            if value.get("available") is False:
                continue
            kind = value.get("capability_type", value.get("type", "skill"))
            if kind not in rows:
                continue
            identifier = value.get("capability_id", value.get("id", value.get("name", "")))
            if identifier:
                rows[kind].append(RoutingCapability(
                    id=identifier, name=value.get("name", identifier),
                    description=value.get("description", ""),
                ))
    return PlanningContext(skills=rows["skill"], experts=rows["expert"])


def _bounded_context(query: str, context: PlanningContext) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    complete = context.context_complete
    preferred = set(context.required_skill_ids) | set(context.required_expert_ids)
    for key in ("required_skill_ids", "required_expert_ids"):
        if getattr(context, key):
            payload[key] = getattr(context, key)
    for kind in ("skills", "experts", "tools"):
        if kind == "experts" and not context.allow_experts:
            continue
        items = getattr(context, kind)
        by_id = {item.id: item for item in items}
        index = LexicalIndex([
            LexicalDocument(key=item.id, name=item.name, description=item.description) for item in items
        ])
        ranked = [item.id for item in items if item.id in preferred]
        ranked.extend(hit.key for hit in index.search_terms(query) if hit.key not in ranked)
        ranked.extend(item.id for item in items if item.id not in ranked)
        payload[kind] = [{"id": key, "name": by_id[key].name[:128], "description": by_id[key].description[:256]}
                         for key in ranked[:32]]
        complete = complete and len(items) <= 32
    payload["context_complete"] = complete
    return payload


def _validate_route(draft: _RouteDraft, context: PlanningContext) -> ExecutionRoute:
    for key in ("skill", "expert", "tool"):
        selected = getattr(draft, f"{key}_ids")
        known = {item.id for item in getattr(context, f"{key}s")}
        if len(set(selected)) != len(selected) or not set(selected).issubset(known):
            raise ValueError(f"Selected {key} IDs must be unique and currently visible")
    if not set(context.required_skill_ids).issubset(draft.skill_ids):
        raise ValueError("A required Skill is unavailable or missing from the route")
    if not set(context.required_expert_ids).issubset(draft.expert_ids):
        raise ValueError("A required expert is unavailable or missing from the route")
    count = len(draft.skill_ids) + len(draft.tool_ids)
    if draft.strategy in {"single_expert", "multi_expert"}:
        if not context.allow_experts:
            raise ValueError("Expert dispatch is unavailable or forbidden")
        requires_planning = True
    else:
        if draft.expert_ids:
            raise ValueError("Host routes cannot select expert assignments")
        if draft.strategy == "direct_answer":
            if count:
                raise ValueError("A direct answer must not require execution capabilities")
            requires_planning = False
        elif draft.strategy == "single_capability":
            if count != 1:
                raise ValueError("A single-capability strategy requires exactly one Skill or host tool")
            requires_planning = False
        else:
            if count < 2:
                raise ValueError("A capability workflow requires multiple Skills or host tools")
            requires_planning = not draft.tool_ids
    return ExecutionRoute(
        **draft.model_dump(), requires_planning=requires_planning, request_id=context.request_id,
    )


async def route_task(
    query: str, *, model: Any, context: PlanningContext,
    inputs: Sequence[PlanningInput], observer: Any = None, feedback: str = "",
    language: str = "cn",
) -> ExecutionRoute:
    """Classify once, with one bounded repair of invalid structured output."""
    if model is None:
        raise ValueError("Symphony routing requires a model.")
    payload: dict[str, Any] = {
        "operation": "task_routing", "query": query,
        "context": _bounded_context(query, context),
        "inputs": [{"name": item.name[:128], "kind": item.kind, "value": item.value[:2000],
                    "description": (item.description or "")[:256],
                    "preview_truncated": (len(item.value) > 2000 or len(item.name) > 128
                                          or len(item.description or "") > 256)} for item in inputs[:32]],
    }
    if len(inputs) > 32 or any(item["preview_truncated"] for item in payload["inputs"]):
        payload["context"]["context_complete"] = False
    if feedback:
        payload["planning_feedback"] = feedback
    strategies = "direct_answer|single_capability|capability_workflow"
    system_prompt = _SYSTEM_PROMPT
    if context.allow_experts:
        strategies += "|single_expert|multi_expert"
        system_prompt += _EXPERT_PROMPT
    system_prompt += (
        f'\nReturn {{"strategy":"{strategies}","reason":"short decision rationale",'
        '"skill_ids":[],"expert_ids":[],"tool_ids":[],"missing_inputs":[]}.\n'
    )
    with model_usage_context("orchestration", "task_routing"):
        for attempt in range(2):
            raw = await invoke_json(
                model, system_prompt=f"{system_prompt}\n{planner_language_instruction(language)}",
                user_content=json.dumps(payload, ensure_ascii=False),
                error_context="Symphony task routing", response_observer=observer,
                request_overrides=thinking_disabled_request_overrides(),
            )
            try:
                return _validate_route(_RouteDraft.model_validate_json(raw), context)
            except ValueError as exc:
                if attempt:
                    raise ValueError("Symphony task routing returned an invalid decision") from exc
                payload["validation_feedback"] = str(exc)
    raise ValueError("Symphony task routing did not return a decision")
