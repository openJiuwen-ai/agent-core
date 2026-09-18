# coding: utf-8
"""技能工具级 invocation 计数（回答审稿：拼接 ≠ 调用）。

SkillUseRail 的 ``skill_mode="all"`` 把 SKILL.md 拼进系统提示，这只证明
技能**出现在上下文**，不证明 agent 调用了 ``skill_tool``。VaG / ASG-SI /
MUSE 都把「执行」当作准入或复用的证据；本模块把 tool_call 从计量日志
里拆出来，与 ``skills_in_prompt`` 对照。

不改变 agent 行为，只读 Harness ``after_tool_call`` / JSONL。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

SKILL_TOOL_NAMES = frozenset({"skill_tool", "list_skill"})


def _as_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
    if isinstance(raw, dict):
        return raw
    get = getattr(raw, "__dict__", None)
    return dict(get) if isinstance(get, dict) else {}


def tool_args_from_ctx(ctx: Any) -> dict[str, Any]:
    """从 Harness tool_call 上下文里防御式抽出参数 dict。"""
    inputs = getattr(ctx, "inputs", None)
    if inputs is None:
        return {}
    for attr in ("arguments", "tool_args", "args", "call_args", "tool_arguments"):
        raw = getattr(inputs, attr, None)
        if raw is None and isinstance(inputs, dict):
            raw = inputs.get(attr)
        parsed = _as_dict(raw)
        if parsed:
            return parsed
    if isinstance(inputs, dict):
        for key in ("skill_name", "name"):
            if inputs.get(key):
                return {"skill_name": inputs[key]}
    return {}


def tool_name_from_ctx(ctx: Any) -> str:
    inputs = getattr(ctx, "inputs", None)
    if inputs is None:
        return ""
    name = getattr(inputs, "tool_name", None) or getattr(inputs, "name", None)
    if not name and isinstance(inputs, dict):
        name = inputs.get("tool_name") or inputs.get("name")
    return str(name or "")


def parse_skill_invocation(tool_name: str, args: Optional[dict[str, Any]] = None) -> Optional[str]:
    """工具名 + 参数 → 被调用的技能名。

    ``skill_tool`` 返回 ``skill_name``；``list_skill`` 记为 ``*``（目录枚举，
    不是对某一条 SKILL.md 的执行）。其它工具返回 None。
    """
    name = (tool_name or "").strip()
    if name not in SKILL_TOOL_NAMES:
        return None
    if name == "list_skill":
        return "*"
    payload = args or {}
    skill = str(payload.get("skill_name") or payload.get("name") or "").strip()
    return skill or None


@dataclass
class InvocationRecord:
    tool_name: str
    skill_name: str
    ts: float = 0.0


@dataclass
class InvocationSummary:
    """拼接 vs 调用对照，供 TaskResult / 审稿 Q1。"""

    n_concatenated: int = 0
    n_invoked: int = 0
    n_list_skill: int = 0
    invoked: list[str] = field(default_factory=list)
    concatenated_unused: list[str] = field(default_factory=list)
    invoked_not_in_prompt: list[str] = field(default_factory=list)
    used_as_tool: bool = False

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def compare_prompt_vs_invoke(
    skills_in_prompt: Iterable[str],
    invoked: Iterable[str],
) -> InvocationSummary:
    """把 ``skills_in_prompt``（拼接）和 tool 级调用拆开。"""
    prompt = [s for s in skills_in_prompt if s]
    raw_inv = [s for s in invoked if s]
    listed = sum(1 for s in raw_inv if s == "*")
    concrete = [s for s in raw_inv if s != "*"]
    prompt_set = set(prompt)
    inv_set = set(concrete)
    return InvocationSummary(
        n_concatenated=len(prompt),
        n_invoked=len(concrete),
        n_list_skill=listed,
        invoked=sorted(inv_set),
        concatenated_unused=sorted(prompt_set - inv_set),
        invoked_not_in_prompt=sorted(inv_set - prompt_set),
        used_as_tool=bool(concrete or listed),
    )


def invocations_from_meter_events(events: Iterable[Any], *, since_ts: float = 0.0,
                                  until_ts: Optional[float] = None) -> list[str]:
    """从 ResourceMeter JSONL 事件提取本段 skill_tool 调用。"""
    names: list[str] = []
    for e in events:
        kind = getattr(e, "kind", None) or (e.get("kind") if isinstance(e, dict) else None)
        if kind != "tool_call":
            continue
        ts = float(getattr(e, "ts", 0.0) if not isinstance(e, dict) else e.get("ts") or 0.0)
        if ts < since_ts:
            continue
        if until_ts is not None and ts > until_ts:
            continue
        tool = getattr(e, "name", "") if not isinstance(e, dict) else e.get("name", "")
        extra = getattr(e, "extra", {}) if not isinstance(e, dict) else e.get("extra") or {}
        parsed = parse_skill_invocation(str(tool), extra if isinstance(extra, dict) else {})
        if parsed:
            names.append(parsed)
        elif str(tool) in SKILL_TOOL_NAMES:
            # 旧日志没有 extra.skill_name：至少记下工具被调过
            names.append("*" if str(tool) == "list_skill" else (str(tool)))
    return names


def count_by_skill(names: Iterable[str]) -> dict[str, int]:
    return dict(Counter(n for n in names if n))
