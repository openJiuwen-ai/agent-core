# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Mine TaskRecords from SessionDigest objects.

Mining walks the ordered ``SessionDigest.turns``: every substantive user
request opens a new segment, later correction / meta / follow-up turns attach
to it, greetings are dropped. Each segment becomes one TaskRecord whose soft
rubric combines the request with the follow-up requirements the user raised
in the same session.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Tuple

from openjiuwen.agent_evolving.skill_train.sleep.types import SessionDigest, TaskRecord

TURN_GREETING = "greeting"
TURN_CORRECTION = "correction"
TURN_META = "meta"
TURN_FOLLOWUP = "followup"  # short continuation that only makes sense with the previous request
TURN_TASK = "task"

_GREETINGS = frozenset(
    {
        "你好",
        "您好",
        "hi",
        "hello",
        "hey",
        "嗨",
        "哈喽",
        "在吗",
        "在么",
        "谢谢",
        "感谢",
        "多谢",
        "thanks",
        "thank you",
        "thx",
        "ok",
        "okay",
        "好的",
        "好",
        "嗯",
        "嗯嗯",
        "行",
        "收到",
        "明白",
        "了解",
        "知道了",
        "早上好",
        "下午好",
        "晚上好",
        "good morning",
        "good afternoon",
        "bye",
        "再见",
    }
)
_STRONG_CORRECTION = (
    "不对",
    "错了",
    "错误",
    "不正确",
    "缺少",
    "少了",
    "漏了",
    "遗漏",
    "不是这个",
    "不是我要的",
    "不完整",
    "重来",
    "wrong",
    "incorrect",
    "missing",
    "not what i",
)
_WEAK_CORRECTION = (
    "需要",
    "应该",
    "还要",
    "还需要",
    "补充",
    "重新",
    "没有",
    "加上",
    "改成",
    "换成",
    "再加",
    "也要",
    "should",
    "need",
    "add",
    "again",
    "instead",
)
_META_QUESTION = ("？", "?", "什么", "哪个", "怎么", "如何", "吗", "what", "which", "how", "did you")
_META_TOPIC = ("skill", "技能", "工具", "tool", "用的", "怎么做", "怎么查", "哪里查", "来源", "数据源")
_CONTINUATION_PREFIX = ("那", "那么", "还有", "再", "也", "然后", "另外", "and ", "what about", "how about", "also")
_CONTINUATION_SUFFIX = ("呢", "呢?", "呢？")
# Shared lead-in for short revisions of the previous answer.
_REV_LEAD = (
    r"(?:请)?(?:帮我|帮忙|麻烦)?"
    r"(?:把|将)?"
    r"(?:上[一条个次]?|刚才|之前的?)?"
    r"(?:回复|答案|结果|输出|内容|数据|表格|字段|列表)?"
    r"(?:里的|中的|里面的|里|中)?"
)
# 增: "缺少/补充/加上/增加 X" → must include X.
_ADD_RE = re.compile(
    r"^(?:"
    r"(?:这个|这|输出|结果)?(?:不对|错了|错误|不好)?[，,。\s]*"
    r"(?:缺少|少了|漏了|遗漏|没有|没给|没加|缺|"
    r"补充|补上|补全|加上|加一下|再加|还要|还需要|需要有|要有|得有|"
    r"增加|添加|新增|加入|附上|附带|给出|给出下|显示|展示|列出|包含|"
    r"带上|带上点|加上点|再给|再给下)"
    r"(?:了|上|一下)?[:：]?\s*(?P<what>.+)"
    r"|"
    rf"{_REV_LEAD}"
    r"(?:补充|补上|加上|增加|添加|新增|加入|附上|显示|展示|列出)\s*(?P<what_ctx>.+)"
    r"|"
    r"(?:please\s+)?(?:add|include|show|provide|append|with|also\s+include)\s+"
    r"(?:the\s+)?(?P<what_en>.+)"
    r")$",
    re.IGNORECASE,
)
# Back-compat alias used by older call sites / tests.
_MISSING_RE = _ADD_RE
# 删: "删除/去掉/不要 X" → must not include X.
# Length-gated in classify so long standalone tasks (删服务器日志…) stay tasks.
_EXCLUDE_RE = re.compile(
    r"^(?:"
    rf"{_REV_LEAD}"
    r"(?:删除|删掉|删去|去掉|移除|剔除|隐去|隐藏|取消|"
    r"不要|别要|别给|别显示|别展示|无需|不用|不需要|省略|略去)\s*(?P<what>.+)"
    r"|"
    r"(?:请)?(?:帮我|帮忙)?"
    r"(?:删除|删掉|删去|去掉|移除|剔除|不要|别要|别显示|无需|不用)\s*(?P<what_bare>.+)"
    r"|"
    r"(?:please\s+)?(?:remove|delete|drop|omit|exclude|hide|without|no more)\s+"
    r"(?:the\s+)?(?P<what_en>.+)"
    r")$",
    re.IGNORECASE,
)
# 改: "改成 X" / "把 A 改成 B" → must change (A) to X/B.
_REPLACE_RE = re.compile(
    r"^(?:"
    rf"{_REV_LEAD}"
    r"(?P<old>.+?)\s*"
    r"(?:改成|改为|换成|换为|替换成|替换为|变成|更新为|调整为)\s*"
    r"(?P<new>.+)"
    r"|"
    r"(?:请)?(?:帮我|帮忙)?"
    r"(?:改成|改为|换成|换为|替换成|替换为|变成|更新为|调整为)\s*(?P<what>.+)"
    r"|"
    r"(?:please\s+)?(?:change|replace|switch|update)\s+"
    r"(?:(?:the\s+)?(?P<old_en>.+?)\s+(?:to|with|into)\s+)?"
    r"(?:the\s+)?(?P<new_en>.+)"
    r")$",
    re.IGNORECASE,
)
_REVISION_MAX_LEN = 30
_TRAILING_PUNCT = "。！!，,；;、 \t"
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# Intent mentions like "使用 tianqi 技能" / "用 tianqi-weather 技能".
_INTENT_SKILL_RE = re.compile(
    r"(?:使用|用)\s*([A-Za-z][A-Za-z0-9_-]*)\s*技能",
)
_EVOLUTION_STORE_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
# Display / spoken aliases → EvolutionStore directory slug.
_SKILL_ALIASES = {
    "tianqi-weather": "tianqi",
}


def _tid(project: str, intent: str) -> str:
    digest = hashlib.sha256((project + "::" + intent).encode("utf-8")).hexdigest()[:12]
    return "task_" + digest


def _short(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _intent_too_short(intent: str) -> bool:
    """Reject empty/near-empty intents; allow short CJK queries like 上海的天气."""
    text = (intent or "").strip()
    if not text:
        return True
    # Whitespace-tokenized English needs a bit more substance.
    if " " in text or "\t" in text:
        return len(text) < 8
    # Single-token / CJK: keep anything with at least 2 characters.
    return len(text) < 2


def _looks_negative(signals: List[str]) -> bool:
    return any(signal.startswith("neg:") for signal in signals)


def _looks_positive(signals: List[str]) -> bool:
    return any(signal.startswith("pos:") for signal in signals)


def _normalize(text: str) -> str:
    return (text or "").strip().strip(_TRAILING_PUNCT + "~～").lower()


def _match_group(match: re.Match[str] | None, *names: str) -> str:
    if match is None:
        return ""
    for name in names:
        try:
            value = match.group(name)
        except IndexError:
            continue
        if value:
            return str(value).strip().rstrip(_TRAILING_PUNCT)
    return ""


def _clean_revision_text(text: str) -> str:
    return (text or "").strip().rstrip(_TRAILING_PUNCT)


def _add_what(text: str) -> str:
    """Return the detail that must be included, or empty."""
    return _match_group(_ADD_RE.match(_clean_revision_text(text)), "what", "what_ctx", "what_en")


def _exclude_what(text: str) -> str:
    """Return the detail that must be excluded, or empty."""
    return _match_group(
        _EXCLUDE_RE.match(_clean_revision_text(text)),
        "what",
        "what_bare",
        "what_en",
    )


def _replace_parts(text: str) -> tuple[str, str]:
    """Return (old, new) for a replace revision; old may be empty."""
    match = _REPLACE_RE.match(_clean_revision_text(text))
    if match is None:
        return "", ""
    old = _match_group(match, "old", "old_en")
    new = _match_group(match, "new", "what", "new_en")
    return old, new


def _looks_like_crud_revision(text: str) -> bool:
    """True when text matches short add / delete / replace revision patterns."""
    return bool(_add_what(text) or _exclude_what(text) or _replace_parts(text)[1])


def classify_user_turn(text: str) -> str:
    """Classify one user turn as greeting / correction / meta / task."""
    raw = (text or "").strip()
    norm = _normalize(raw)
    if not norm:
        return TURN_GREETING
    # Exact match only: startswith false-positives on short CJK tasks (e.g. 你好世界).
    if norm in _GREETINGS:
        return TURN_GREETING
    lower = raw.lower()
    if any(marker in lower for marker in _STRONG_CORRECTION):
        return TURN_CORRECTION
    # Short 增/删/改 revisions of the previous answer.
    if len(norm) <= _REVISION_MAX_LEN and _looks_like_crud_revision(raw):
        return TURN_CORRECTION
    if len(norm) <= 15:
        if any(marker in lower for marker in _WEAK_CORRECTION):
            return TURN_CORRECTION
        if norm.startswith(("这个", "不")):
            return TURN_CORRECTION
    if any(q in lower for q in _META_QUESTION) and any(t in lower for t in _META_TOPIC):
        return TURN_META
    if len(norm) <= 12 and (
        lower.startswith(_CONTINUATION_PREFIX) or raw.rstrip(_TRAILING_PUNCT).endswith(_CONTINUATION_SUFFIX)
    ):
        return TURN_FOLLOWUP
    return TURN_TASK


@dataclass
class Segment:
    """One user request plus the follow-ups and replies that belong to it."""

    intent: str
    follow_ups: List[Tuple[str, str]] = field(default_factory=list)  # (kind, text)
    assistant_replies: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)

    @property
    def has_correction(self) -> bool:
        return any(kind == TURN_CORRECTION for kind, _ in self.follow_ups)


def _turns_from_digest(digest: SessionDigest) -> List[Dict[str, Any]]:
    if digest.turns:
        return [dict(turn) for turn in digest.turns if isinstance(turn, dict)]
    # Legacy digest without ordered turns: interleave user/assistant by index.
    out: List[Dict[str, Any]] = []
    finals = list(digest.assistant_finals or [])
    for index, prompt in enumerate(digest.user_prompts or []):
        out.append({"role": "user", "content": prompt, "skills": []})
        if index < len(finals):
            out.append({"role": "assistant", "content": finals[index], "skills": []})
    leftover_start = len(digest.user_prompts or [])
    for extra in finals[leftover_start:]:
        out.append({"role": "assistant", "content": extra, "skills": []})
    return out


def segment_digest(digest: SessionDigest) -> List[Segment]:
    """Split one session into task segments.

    A ``task`` turn opens a new segment; ``correction`` / ``meta`` /
    ``followup`` turns attach to the current segment (or open one when nothing
    precedes them); ``greeting`` turns are dropped. Assistant and tool turns
    belong to the current segment.
    """
    segments: List[Segment] = []
    current: Segment | None = None
    for turn in _turns_from_digest(digest):
        role = str(turn.get("role") or "")
        content = str(turn.get("content") or "").strip()
        if role == "user":
            if not content:
                continue
            kind = classify_user_turn(content)
            if kind == TURN_GREETING:
                continue
            if kind == TURN_TASK or current is None:
                current = Segment(intent=content)
                segments.append(current)
                continue
            current.follow_ups.append((kind, content))
        elif role == "assistant":
            if current is not None and content:
                current.assistant_replies.append(content)
        elif role == "tool":
            if current is None:
                continue
            if content and content not in current.tools:
                current.tools.append(content)
            for skill in turn.get("skills") or []:
                skill = str(skill or "").strip()
                if skill and skill not in current.skills:
                    current.skills.append(skill)
    return segments


def _rewrite_follow_up(kind: str, text: str, *, cjk: bool) -> str:
    clean = text.strip().rstrip(_TRAILING_PUNCT)
    if kind == TURN_META:
        return f"需回应用户追问：{clean}" if cjk else f"Must answer the user's follow-up question: {clean}"
    if kind == TURN_FOLLOWUP:
        return f"需同时覆盖后续请求：{clean}" if cjk else f"Must also cover the follow-up request: {clean}"
    # Prefer 改 → 删 → 增 so "改成…" is not swallowed by add/delete verbs.
    old, new = _replace_parts(clean)
    if new:
        if old:
            return (
                f"必须将：{old} 改为 {new}"
                if cjk
                else f"Must change {old} to {new}"
            )
        return f"必须改为：{new}" if cjk else f"Must change to: {new}"
    excluded = _exclude_what(clean)
    if excluded:
        return f"必须不包含：{excluded}" if cjk else f"Must not include: {excluded}"
    added = _add_what(clean)
    if added:
        return f"必须包含：{added}" if cjk else f"Must include: {added}"
    return clean


def build_rubric(segment: Segment) -> str:
    """Build a structured soft rubric from the request and its follow-ups."""
    if not segment.follow_ups:
        return ""
    cjk = bool(_CJK_RE.search(segment.intent))
    lines: List[str] = []
    seen: set[str] = set()
    for kind, text in segment.follow_ups:
        # Bare acknowledgements such as "需要" / "yes" carry no checkable requirement.
        if kind == TURN_CORRECTION and len(_normalize(text)) <= 3:
            continue
        line = _rewrite_follow_up(kind, _short(text, 200), cjk=cjk)
        if line and line not in seen:
            seen.add(line)
            lines.append("- " + line)
    if not lines:
        return ""
    if cjk:
        head = f"用户任务：{segment.intent}\n用户在同一会话中随后提出的要求（回答必须满足）："
    else:
        head = (
            f"User task: {segment.intent}\n"
            "Requirements the user raised later in the same session (the answer must satisfy them):"
        )
    return head + "\n" + "\n".join(lines)


def _normalize_skill_slug(skill: str) -> str:
    """Map spoken / alias names to an EvolutionStore-safe directory slug."""
    text = (skill or "").strip()
    if not text:
        return ""
    mapped = _SKILL_ALIASES.get(text.lower(), text)
    if not _EVOLUTION_STORE_SKILL_NAME_RE.match(mapped):
        return ""
    return mapped


def _skill_from_intent(intent: str) -> str:
    match = _INTENT_SKILL_RE.search(intent or "")
    if not match:
        return ""
    return _normalize_skill_slug(match.group(1))


def _segment_skill_hint(segment: Segment, digest: SessionDigest) -> str:
    """Resolve a single skill slug for this segment.

    Priority:
    1. Skills attached to tool turns inside the segment (exactly one).
    2. Session-level ``skills_used`` when it is unambiguous (exactly one).
    3. Skill name parsed from the user intent (e.g. ``使用 tianqi 技能``).

    Returns empty when none of the signals uniquely identify a store-safe skill.
    """
    unique = list(dict.fromkeys(_normalize_skill_slug(s) for s in segment.skills if s))
    unique = [s for s in unique if s]
    if len(unique) == 1:
        return unique[0]

    session = list(
        dict.fromkeys(_normalize_skill_slug(s) for s in (digest.skills_used or []) if s)
    )
    session = [s for s in session if s]
    if len(session) == 1:
        return session[0]

    return _skill_from_intent(segment.intent)


def _segment_outcome(segment: Segment, digest: SessionDigest) -> str:
    if segment.has_correction or _looks_negative(digest.feedback_signals):
        return "fail"
    if _looks_positive(digest.feedback_signals):
        return "success"
    if len(segment.follow_ups) >= 2:
        return "mixed"
    return "unknown"


def _segment_correction_tags(segment: Segment) -> List[str]:
    """Tag CRUD correction kinds mined from follow-ups (exclude/add/replace)."""
    tags: List[str] = []
    for kind, text in segment.follow_ups:
        if kind != TURN_CORRECTION:
            continue
        old, new = _replace_parts(text)
        if new:
            tags.append("correction:replace")
            continue
        excluded = _exclude_what(text)
        if excluded:
            tags.append("correction:exclude")
            tags.append(f"exclude:{_short(excluded, 40)}")
            continue
        added = _add_what(text)
        if added:
            tags.append("correction:add")
            tags.append(f"add:{_short(added, 40)}")
    return list(dict.fromkeys(tags))


def _tags_for(digest: SessionDigest, tools: List[str], segment: Segment | None = None) -> List[str]:
    tags: List[str] = []
    if tools:
        tags.append("tools:" + "+".join(tools[:4]))
    if digest.trace_id:
        tags.append("trajectory:" + digest.trace_id)
    if segment is not None:
        tags.extend(_segment_correction_tags(segment))
    return list(dict.fromkeys(tags))


def correction_kinds_from_task(task: TaskRecord) -> List[str]:
    """Return correction kinds for a mined task: exclude / add / replace."""
    kinds: List[str] = []
    for tag in task.tags or ():
        if tag.startswith("correction:"):
            kind = tag.split(":", 1)[1].strip()
            if kind:
                kinds.append(kind)
    if kinds:
        return list(dict.fromkeys(kinds))
    blob = f"{task.reference or ''}\n{task.context_excerpt or ''}"
    if re.search(r"必须不包含|Must not include", blob):
        kinds.append("exclude")
    if re.search(r"必须包含|Must include", blob):
        kinds.append("add")
    if re.search(r"必须将：|必须改为：|Must change", blob):
        kinds.append("replace")
    return list(dict.fromkeys(kinds))


def exclude_targets_from_task(task: TaskRecord) -> List[str]:
    """Targets the user asked to omit (from tags and rubric lines)."""
    targets: List[str] = []
    for tag in task.tags or ():
        if tag.startswith("exclude:"):
            value = tag.split(":", 1)[1].strip()
            if value:
                targets.append(value)
    blob = task.reference or ""
    for match in re.finditer(r"(?:必须不包含|Must not include)[:：]\s*(.+)", blob):
        value = match.group(1).strip().rstrip("。．.")
        if value:
            targets.append(value)
    return list(dict.fromkeys(targets))


def heuristic_mine(digests: List[SessionDigest], *, max_tasks: int = 40) -> List[TaskRecord]:
    """One TaskRecord per task segment across all digests, capped at ``max_tasks``."""
    tasks: List[TaskRecord] = []
    for digest in digests:
        for segment in segment_digest(digest):
            intent = segment.intent
            if _intent_too_short(intent):
                continue
            rubric = build_rubric(segment)
            context = ""
            if segment.follow_ups:
                context = "Follow-up constraints from the same session:\n- " + "\n- ".join(
                    _short(text, 200) for _kind, text in segment.follow_ups[:3]
                )
            attempted = segment.assistant_replies[-1] if segment.assistant_replies else ""
            skill_hint = _segment_skill_hint(segment, digest)
            if not skill_hint:
                # No resolvable skill name → do not mine / do not set skill_hint.
                continue
            key = intent + "\n" + rubric if rubric else intent
            tasks.append(
                TaskRecord(
                    id=_tid(digest.project, key),
                    project=digest.project,
                    intent=_short(intent, 800),
                    context_excerpt=_short(context, 600),
                    attempted_solution=_short(attempted, 600),
                    outcome=_segment_outcome(segment, digest),
                    reference_kind="rubric" if rubric else "none",
                    reference=rubric,
                    tags=_tags_for(
                        digest,
                        segment.tools or list(digest.tools_used or []),
                        segment,
                    ),
                    source_traces=[digest.trace_id],
                    skill_hint=skill_hint,
                )
            )
            if len(tasks) >= max_tasks:
                return tasks
    return tasks


def dedup_tasks(tasks: List[TaskRecord]) -> List[TaskRecord]:
    by_id: dict[str, TaskRecord] = {}
    hints_by_id: dict[str, set[str]] = {}
    for task in tasks:
        if task.skill_hint:
            hints_by_id.setdefault(task.id, set()).add(task.skill_hint)
        if task.id in by_id:
            existing = by_id[task.id]
            existing.source_traces = list(dict.fromkeys(existing.source_traces + task.source_traces))
            order = {"success": 3, "fail": 2, "mixed": 1, "unknown": 0}
            if order.get(task.outcome, 0) > order.get(existing.outcome, 0):
                existing.outcome = task.outcome
        else:
            by_id[task.id] = replace(task, source_traces=list(task.source_traces))
    for task_id, task in by_id.items():
        hints = hints_by_id.get(task_id, set())
        task.skill_hint = next(iter(hints)) if len(hints) == 1 else ""
    return list(by_id.values())


def group_tasks_by_skill_hint(
    tasks: List[TaskRecord],
) -> Dict[str, List[TaskRecord]]:
    """Group mined tasks by skill hint (first-seen order).

    Only tasks with a single non-empty ``skill_hint`` are kept. Empty or
    conflicting hints are skipped — sleep never invents a fallback skill.
    """
    observed: dict[str, set[str]] = {}
    for task in tasks:
        observed.setdefault(task.id, set()).add((task.skill_hint or "").strip())

    copied = [replace(task, source_traces=list(task.source_traces)) for task in tasks]
    groups: Dict[str, List[TaskRecord]] = {}
    for task in dedup_tasks(copied):
        hints = {h for h in observed.get(task.id, set()) if h}
        if len(hints) != 1:
            continue
        hint = next(iter(hints))
        task.skill_hint = hint
        groups.setdefault(hint, []).append(task)
    return groups


def assign_splits(
    tasks: List[TaskRecord],
    *,
    val_fraction: float = 0.34,
    test_fraction: float = 0.0,
    seed: int = 42,
) -> List[TaskRecord]:
    if not 0.0 <= val_fraction <= 1.0 or not 0.0 <= test_fraction <= 1.0:
        raise ValueError("val_fraction/test_fraction must be in [0, 1]")
    if val_fraction + test_fraction >= 1.0:
        raise ValueError("val_fraction + test_fraction must be < 1")

    dream = [task for task in tasks if task.origin == "dream"]
    real = [task for task in tasks if task.origin != "dream"]
    for task in dream:
        task.split = "train"

    val_cut = int(round(val_fraction * 100))
    test_cut = val_cut + int(round(test_fraction * 100))

    def _stable_key(task: TaskRecord) -> tuple[int, str]:
        bucket = int(hashlib.sha256((str(seed) + task.id).encode()).hexdigest(), 16)
        return bucket, task.id

    for task in real:
        bucket = _stable_key(task)[0] % 100
        if bucket < val_cut:
            task.split = "val"
        elif bucket < test_cut:
            task.split = "test"
        else:
            task.split = "train"

    # Ensure non-empty train/val when possible for small batches.
    if real and not any(task.split == "train" for task in real):
        sorted(real, key=_stable_key)[0].split = "train"
    if real and val_fraction > 0 and not any(task.split == "val" for task in real):
        candidates = [task for task in real if task.split == "train"]
        if candidates:
            sorted(candidates, key=_stable_key)[0].split = "val"
    return tasks


def mine_capped(
    digests: List[SessionDigest],
    *,
    max_tasks: int = 40,
    val_fraction: float = 0.34,
    test_fraction: float = 0.0,
    seed: int = 42,
) -> tuple[List[TaskRecord], bool]:
    """Mine tasks and report whether ``max_tasks`` truncated the scan.

    ``truncated`` is True when ``heuristic_mine`` stopped early after filling
    ``max_tasks`` slots (checked on the pre-dedup list). Callers that advance a
    harvest watermark must not use task timestamps alone when truncated — later
    digests may have been skipped.
    """
    cap = max(int(max_tasks or 0), 0)
    raw = heuristic_mine(digests, max_tasks=cap)
    # Pre-dedup length hits the cap ⇒ heuristic_mine returned early; later
    # digests may be unscanned. Exact-fill with no leftovers is also treated as
    # truncated (conservative) so harvest watermarks do not skip unread traces.
    truncated = cap > 0 and len(raw) >= cap
    tasks = assign_splits(
        dedup_tasks(raw),
        val_fraction=val_fraction,
        test_fraction=test_fraction,
        seed=seed,
    )
    return tasks, truncated


def mine(
    digests: List[SessionDigest],
    *,
    max_tasks: int = 40,
    val_fraction: float = 0.34,
    test_fraction: float = 0.0,
    seed: int = 42,
) -> List[TaskRecord]:
    tasks, _truncated = mine_capped(
        digests,
        max_tasks=max_tasks,
        val_fraction=val_fraction,
        test_fraction=test_fraction,
        seed=seed,
    )
    return tasks
