"""Internal execution strategy selection used by the public planning entrypoint."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.symphony.orchestration.contracts import PlanningInput
from openjiuwen.symphony.orchestration.model import invoke_json, model_usage_context
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


_SYSTEM_PROMPT = """
Choose one supplied execution strategy. Decide who should own the work before
selecting capabilities. Default to main-agent execution.

Group work by the context it needs: original sources, prior decisions, and
working state. Give steps that repeatedly need the same context one owner.
A sequence of steps that produces or revises the same result normally belongs
to one work unit.

Honor delegation limits and required capabilities. Select only visible IDs
from the corresponding catalog: skills in skill_ids, tools in tool_ids, and
experts in expert_ids. List each selected ID once.

direct_answer: Answer from known information. Leave all capability ID lists empty.
single_capability: Use exactly one Skill or host tool. A Skill may use tools internally.
capability_workflow: The main agent coordinates two or more Skills or host tools
to process a batch, produce alternate formats, or update a shared working result.
For these three strategies, leave expert_ids empty.

Treat file references, truncated previews, and partial catalogs as incomplete
evidence. Identify genuinely missing material and unavailable required capabilities.
Keep the route feasible given the capabilities and permissions actually available.
"""

_EXPERT_PROMPT = """
When delegation is allowed, evaluate the work before inspecting the expert
catalog. If it qualifies for delegation, use an expert route even when no
installed expert is listed or suitable. The planner can create task-specific
inline roles. In that case, use single_expert or multi_expert as appropriate
and leave expert_ids empty when there are no matching IDs.

Choose an expert route if the user requires an expert assignment or at least
one work unit has a clear benefit from separate ownership:

- Context isolation: The unit must examine substantial local material, while
  downstream work needs only a compact set of findings, evidence references,
  applicable conditions, and unresolved questions. The expert can complete
  the unit using a defined set of sources, and the recipient can act or verify
  the findings without rereading most of those sources. This can justify one
  expert even when the unit is on a sequential path.
- Parallel work: Units can make meaningful progress concurrently and return
  independently verifiable results. They may share read-only requirements
  or sources. Put interleaved writes and tightly coupled decisions under one owner.
- Specialist role: The unit calls for a distinct domain judgment, toolset,
  or working rule that materially changes how the work is done. State the
  judgment or behavior the role adds.
- Independent verification: A separate reviewer can assess a specified result
  against original sources and acceptance criteria using the artifact or state
  under review and a small amount of additional context.

Choose single_expert when one delegated assignment is sufficient, and
multi_expert when multiple assignments are justified. Use a matching visible
expert when available. Honor any required expert IDs. For expert routes,
include only required host capabilities.

In reason, identify the work to delegate, the benefit of independent ownership,
and the result the main agent needs back. If the proposed split requires
repeatedly transferring the same evidence, decisions, or state, keep that
work with one owner.
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
            )
            try:
                return _validate_route(_RouteDraft.model_validate_json(raw), context)
            except ValueError as exc:
                if attempt:
                    raise ValueError("Symphony task routing returned an invalid decision") from exc
                payload["validation_feedback"] = str(exc)
    raise ValueError("Symphony task routing did not return a decision")
