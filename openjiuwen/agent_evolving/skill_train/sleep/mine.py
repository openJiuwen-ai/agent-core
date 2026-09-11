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
_MISSING_RE = re.compile(
    r"^(?:这个|这)?(?:不对|错了)?[，,。\s]*(?:缺少|少了|漏了|遗漏|没有|缺)(?:了)?[:：]?\s*(?P<what>.+)$"
)
_TRAILING_PUNCT = "。！!，,；;、 \t"
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


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


def session_skill_hint(digest: SessionDigest) -> str:
    skills = [skill for skill in (digest.skills_used or []) if skill]
    unique = list(dict.fromkeys(skills))
    return unique[0] if len(unique) == 1 else ""


def _normalize(text: str) -> str:
    return (text or "").strip().strip(_TRAILING_PUNCT + "~～").lower()


def classify_user_turn(text: str) -> str:
    """Classify one user turn as greeting / correction / meta / task."""
    raw = (text or "").strip()
    norm = _normalize(raw)
    if not norm:
        return TURN_GREETING
    if len(norm) <= 6 and (norm in _GREETINGS or any(norm.startswith(g) for g in _GREETINGS if len(g) >= 2)):
        return TURN_GREETING
    lower = raw.lower()
    if any(marker in lower for marker in _STRONG_CORRECTION):
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
    for extra in finals[len(digest.user_prompts or []) :]:
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
    match = _MISSING_RE.match(clean)
    if match:
        what = match.group("what").strip().rstrip(_TRAILING_PUNCT)
        if what:
            return f"必须包含：{what}" if cjk else f"Must include: {what}"
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


def _segment_skill_hint(segment: Segment, digest: SessionDigest) -> str:
    unique = list(dict.fromkeys(skill for skill in segment.skills if skill))
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return session_skill_hint(digest)
    return ""


def _segment_outcome(segment: Segment, digest: SessionDigest) -> str:
    if segment.has_correction or _looks_negative(digest.feedback_signals):
        return "fail"
    if _looks_positive(digest.feedback_signals):
        return "success"
    if len(segment.follow_ups) >= 2:
        return "mixed"
    return "unknown"


def _tags_for(digest: SessionDigest, tools: List[str]) -> List[str]:
    tags: List[str] = []
    if tools:
        tags.append("tools:" + "+".join(tools[:4]))
    if digest.trajectory_id:
        tags.append("trajectory:" + digest.trajectory_id)
    return tags


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
                    tags=_tags_for(digest, segment.tools or list(digest.tools_used or [])),
                    source_sessions=[digest.session_id],
                    skill_hint=_segment_skill_hint(segment, digest),
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
            existing.source_sessions = list(dict.fromkeys(existing.source_sessions + task.source_sessions))
            order = {"success": 3, "fail": 2, "mixed": 1, "unknown": 0}
            if order.get(task.outcome, 0) > order.get(existing.outcome, 0):
                existing.outcome = task.outcome
        else:
            by_id[task.id] = replace(task, source_sessions=list(task.source_sessions))
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

    copied = [replace(task, source_sessions=list(task.source_sessions)) for task in tasks]
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


def mine(
    digests: List[SessionDigest],
    *,
    max_tasks: int = 40,
    val_fraction: float = 0.34,
    test_fraction: float = 0.0,
    seed: int = 42,
) -> List[TaskRecord]:
    tasks = heuristic_mine(digests, max_tasks=max_tasks)
    tasks = dedup_tasks(tasks)
    return assign_splits(
        tasks,
        val_fraction=val_fraction,
        test_fraction=test_fraction,
        seed=seed,
    )
