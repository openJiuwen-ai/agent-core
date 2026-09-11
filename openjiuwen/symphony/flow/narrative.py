"""叙事蒸馏：任务描述与执行过程文本（LLM 优先，模板降级）。"""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.symphony.flow.distill import topological_order
from openjiuwen.symphony.flow.models import RecipeEvidence

NARRATIVE_SOURCE_LLM = "llm"
NARRATIVE_SOURCE_TEMPLATE = "template"

_MEMBER_DEFAULT_DESCRIPTION = "协作成员能力"

_SYSTEM_PROMPT = (
    "你是编排经验蒸馏助手。根据历史任务请求和已验证的能力组合结构，"
    "归纳任务类型描述、适用触发条件、示例请求与执行过程说明。要求："
    "只依据给定材料，不得虚构未出现的能力、步骤或结论；"
    "不得把相关性表述为因果关系；示例请求必须是对历史请求的改写归纳，"
    "不得原样复制。只输出 JSON 对象，字段为 "
    '{"task_description": str, "trigger_conditions": str, '
    '"example_requests": [str], "execution_narrative": str}。'
)


def _member_description(
    capability_id: str,
    capability_infos: dict[str, Any] | None,
) -> str:
    info = (capability_infos or {}).get(capability_id)
    if not isinstance(info, dict):
        return _MEMBER_DEFAULT_DESCRIPTION
    text = str(info.get("description") or "").strip()
    return text or _MEMBER_DEFAULT_DESCRIPTION


def _distinct_queries(records: list[RecipeEvidence], *, limit: int) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for record in records:
        query = record.query.strip()
        if query and query not in seen:
            seen.add(query)
            queries.append(query)
    return queries[:limit]


async def distill_texts(
    records: list[RecipeEvidence],
    *,
    skill_pack: dict[str, Any],
    max_examples: int,
    llm_client: Any | None = None,
    capability_infos: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """蒸馏 applicability 与 execution_narrative；LLM 失败时模板降级。"""

    order = topological_order(skill_pack)
    members = [
        {
            "id": capability_id,
            "description": _member_description(capability_id, capability_infos),
        }
        for capability_id in order
    ]
    if llm_client is not None:
        try:
            return await _distill_with_llm(
                records,
                members=members,
                order=order,
                max_examples=max_examples,
                llm_client=llm_client,
            )
        except Exception:  # noqa: BLE001
            pass
    return _template_texts(records, members=members, order=order)


async def _distill_with_llm(
    records: list[RecipeEvidence],
    *,
    members: list[dict[str, str]],
    order: list[str],
    max_examples: int,
    llm_client: Any,
) -> dict[str, Any]:
    user_content = json.dumps(
        {
            "queries": _distinct_queries(records, limit=max_examples * 2),
            "steps": order,
            "members": members,
        },
        ensure_ascii=False,
    )
    raw = await llm_client.complete_json_async(
        system_prompt=_SYSTEM_PROMPT,
        user_content=user_content,
        error_context="CapabilityFlow narrative",
    )
    payload = json.loads(raw)
    task_description = str(payload.get("task_description") or "").strip()
    narrative = str(payload.get("execution_narrative") or "").strip()
    if not task_description or not narrative:
        raise ValueError("narrative distillation produced empty text")
    example_requests = [str(item).strip() for item in (payload.get("example_requests") or []) if str(item).strip()]
    return {
        "applicability": {
            "task_description": task_description,
            "trigger_conditions": str(payload.get("trigger_conditions") or "").strip(),
            "example_requests": example_requests[:max_examples],
        },
        "execution_narrative": narrative,
        "narrative_source": NARRATIVE_SOURCE_LLM,
    }


def _template_texts(
    records: list[RecipeEvidence],
    *,
    members: list[dict[str, str]],
    order: list[str],
) -> dict[str, Any]:
    queries = _distinct_queries(records, limit=3)
    task_description = f"基于 {len(order)} 个能力协作完成的任务（{' → '.join(order)}）"
    trigger_conditions = "用户请求需要多个检索、整理或生成能力接力完成，且任务形态与历史成功执行相似。"
    example_requests = [f"请帮我完成类似任务：{query}" for query in queries]
    narrative_steps = "；".join(f"由能力 {member['id']}（{member['description']}）接力执行" for member in members)
    execution_narrative = (
        f"该组合共执行 {len(records)} 次，成功 "
        f"{sum(1 for record in records if record.outcome == 'success')} 次。"
        f"执行过程：{narrative_steps}。"
    )
    return {
        "applicability": {
            "task_description": task_description,
            "trigger_conditions": trigger_conditions,
            "example_requests": example_requests,
        },
        "execution_narrative": execution_narrative,
        "narrative_source": NARRATIVE_SOURCE_TEMPLATE,
    }
