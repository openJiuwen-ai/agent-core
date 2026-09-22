# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Load JiuwenSwarm ``traces-*.jsonl`` into SessionDigest rows grouped by traceId."""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from openjiuwen.agent_evolving.skill_train.sleep.types import SessionDigest
from openjiuwen.agent_evolving.trajectory.spans import attributes_to_map
from openjiuwen.extensions.observability import semconv

_USER_ENVELOPE_PREFIX = "你收到一条消息："
_REVIEW_USER_MARKERS = (
    "判断「待判定的用户消息」",
    "判断「待判定的用户消息」是否包含",
    "是否包含对对话中已使用 skill",
)
# Internal skill_train / optimizer briefs sometimes land in llm.call as role=user.
# They are not JiuwenSwarm chat turns and must never become SessionDigest intents.
_META_USER_MARKERS = (
    "Skill 优化分析专家",
    "候选演进经验",
    "完成根因归因",
    "You are the skill_train optimizer",
    "You are the target agent replaying a harvested task",
)
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>", re.IGNORECASE)
_INDEXED_ATTR_RE = re.compile(r"^(?P<base>.+)\.(?P<index>\d+)\.(?P<field>[^.]+)$")


def has_jiuwenswarm_traces(path: Path) -> bool:
    path = Path(path).expanduser()
    if path.is_file():
        return path.name.startswith("traces-") and path.suffix == ".jsonl"
    if not path.is_dir():
        return False
    return any(path.glob("traces-*.jsonl"))


def _trace_files(path: Path) -> list[Path]:
    path = Path(path).expanduser()
    if path.is_file():
        return [path]
    return sorted(path.glob("traces-*.jsonl"))


def _decode_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in '{["':
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _indexed_messages(attrs: Mapping[str, Any], base: str) -> list[dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    prefix = f"{base}."
    for key, value in attrs.items():
        if not key.startswith(prefix):
            continue
        match = _INDEXED_ATTR_RE.match(key)
        if not match or match.group("base") != base:
            continue
        index = int(match.group("index"))
        indexed.setdefault(index, {})[match.group("field")] = _decode_jsonish(value)
    ordered: list[dict[str, Any]] = []
    for index in sorted(indexed):
        row = indexed.get(index)
        if row is not None:
            ordered.append(row)
    return ordered


def _span_attrs(span: Mapping[str, Any]) -> dict[str, Any]:
    return attributes_to_map(span.get("attributes"))


def _session_id_from_attrs(attrs: Mapping[str, Any]) -> str:
    return str(attrs.get("agentteam.session.id") or attrs.get("session.id") or "").strip()


def _trace_id_from_span(span: Mapping[str, Any]) -> str:
    return str(span.get("traceId") or span.get("trace_id") or "").strip()


def _spans_look_like_noise(spans: list[dict[str, Any]]) -> bool:
    """Skip prewarm / heartbeat traces even when the group key is a traceId."""
    for span in spans:
        sid = _session_id_from_attrs(_span_attrs(span))
        if sid.startswith("__prewarm__") or sid.startswith("heartbeat_"):
            return True
    return False


def _start_time(span: Mapping[str, Any]) -> int:
    raw = span.get("startTimeUnixNano") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _iter_spans_from_record(record: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    for resource_span in record.get("resourceSpans") or []:
        if not isinstance(resource_span, Mapping):
            continue
        for scope_span in resource_span.get("scopeSpans") or []:
            if not isinstance(scope_span, Mapping):
                continue
            for span in scope_span.get("spans") or []:
                if isinstance(span, Mapping):
                    yield dict(span)


def _strip_system_reminder(text: str) -> str:
    return _SYSTEM_REMINDER_RE.sub("", text or "").strip()


def _unwrap_user_content(text: str) -> str | None:
    """Return cleaned user text, or None when the turn should be dropped."""
    raw = _strip_system_reminder(text)
    if not raw:
        return None
    if any(marker in raw for marker in _REVIEW_USER_MARKERS):
        return None
    if any(marker in raw for marker in _META_USER_MARKERS):
        return None
    if raw.startswith("这是一次心跳请求任务") or "<heartbeat_user_task>" in raw:
        return None

    body = raw
    if body.startswith(_USER_ENVELOPE_PREFIX):
        body = body[len(_USER_ENVELOPE_PREFIX):].strip()
    parsed = _decode_jsonish(body)
    if isinstance(parsed, dict):
        source = str(parsed.get("source") or "").strip()
        if source.startswith("__prewarm__") or source == "heartbeat":
            return None
        msg_type = str(parsed.get("type") or "").strip().lower()
        if msg_type and msg_type not in {"user input", "user_input", "user"}:
            return None
        content = parsed.get("content")
        if content is None:
            return None
        cleaned = _strip_system_reminder(str(content)).strip()
        if cleaned.startswith("这是一次心跳请求任务") or "<heartbeat_user_task>" in cleaned:
            return None
        if any(marker in cleaned for marker in _META_USER_MARKERS):
            return None
        return cleaned or None
    cleaned = body.strip()
    if any(marker in cleaned for marker in _META_USER_MARKERS):
        return None
    return cleaned or None


def _prompt_messages(attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    messages = _indexed_messages(attrs, semconv.LANGFUSE_GEN_AI_PROMPT)
    if not messages:
        messages = _indexed_messages(attrs, semconv.GEN_AI_PROMPT)
    return messages


def _completion_messages(attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    messages = _indexed_messages(attrs, semconv.LANGFUSE_GEN_AI_COMPLETION)
    if not messages:
        messages = _indexed_messages(attrs, semconv.GEN_AI_COMPLETION)
    if messages:
        return messages
    output = attrs.get(semconv.LANGFUSE_OBSERVATION_OUTPUT)
    if isinstance(output, str) and output.strip():
        return [{"role": "assistant", "content": output.strip()}]
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict)]
    return []


def _has_user_envelope(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if str(message.get("role") or "") != "user":
            continue
        content = str(message.get("content") or "")
        if _USER_ENVELOPE_PREFIX not in content:
            continue
        if _unwrap_user_content(content):
            return True
    return False


def _has_clean_user(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if str(message.get("role") or "") != "user":
            continue
        if _unwrap_user_content(str(message.get("content") or "")):
            return True
    return False


def _select_main_llm_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    llm_spans = [span for span in spans if str(span.get("name") or "").strip() == "llm.call"]
    llm_spans.sort(key=_start_time)

    with_envelope: list[tuple[int, int, dict[str, Any]]] = []
    with_user: list[tuple[int, int, dict[str, Any]]] = []
    for span in llm_spans:
        attrs = _span_attrs(span)
        prompt = _prompt_messages(attrs)
        prompt_len = sum(len(str(m.get("content") or "")) for m in prompt)
        if _has_user_envelope(prompt):
            with_envelope.append((_start_time(span), prompt_len, span))
        elif _has_clean_user(prompt):
            with_user.append((_start_time(span), prompt_len, span))

    # Prefer real JiuwenSwarm chat envelopes. Among candidates, take the latest
    # call (full multi-turn history). Do NOT prefer max prompt_len among plain
    # user spans — internal optimizer briefs are often longer and later.
    if with_envelope:
        return max(with_envelope, key=lambda item: (item[0], item[1]))[2]
    if with_user:
        return max(with_user, key=lambda item: (item[0], -item[1]))[2]
    return None


def _is_valid_skill_name(skill: str) -> bool:
    text = (skill or "").strip()
    if not text or len(text) > 128:
        return False
    if text in {"skill_tool", "skill", "load_skill", "use_skill"}:
        return False
    # Reject path/markup noise and polluted slices from skill_content blobs.
    if any(ch in text for ch in "<>{}[]()/\\'\"\n\r\t"):
        return False
    if "skill_content" in text or "skill_directory" in text:
        return False
    return True


# Matches EvolutionStore.create_skill / skills/<dir> folder identity.
_EVOLUTION_STORE_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _is_evolution_store_skill_name(skill: str) -> bool:
    """True when ``skill`` is a valid EvolutionStore / skills/ directory name."""
    return bool(_EVOLUTION_STORE_SKILL_NAME_RE.match((skill or "").strip()))


_SKILL_PATH_KEYS = frozenset(
    {
        "skill_directory",
        "skill_dir",
        "skill_path",
        "directory",
        "path",
    }
)

# Frontmatter ``name:`` inside skill_tool output skill_content.
_FRONTMATTER_NAME_RE = re.compile(
    r"(?m)^name:\s*(?:[\"'](.+?)[\"']|([^\n#]+?))\s*$",
)


def _skill_from_directory_path(path: str) -> str | None:
    """Extract a skill folder name from a path that contains a ``skills`` segment."""
    text = (path or "").replace("\\", "/").strip().strip("'\"")
    if not text:
        return None
    parts = [part for part in text.split("/") if part]
    for index, part in enumerate(parts):
        if part.lower() == "skills" and index + 1 < len(parts):
            candidate = parts[index + 1].strip().strip("'\"")
            # Directory basenames are usually slug-like; drop trailing junk.
            for stopper in ("'", '"', ","):
                if stopper in candidate:
                    candidate = candidate.split(stopper, 1)[0].strip()
            if _is_valid_skill_name(candidate):
                return candidate
    return None


def _skill_from_skill_content(content: str) -> str | None:
    """Read skill display name from SKILL.md-style frontmatter in tool output."""
    text = (content or "").strip()
    if not text:
        return None
    match = _FRONTMATTER_NAME_RE.search(text)
    if not match:
        return None
    candidate = (match.group(1) or match.group(2) or "").strip().strip("'\"")
    if _is_valid_skill_name(candidate):
        return candidate
    return None


def _skills_from_tool_input(raw: Any) -> list[str]:
    """Extract ``skill_name`` from skill_tool arguments.

    JiuwenSwarm typically records::

        [[{"skill_name": "天气"}], {"session": "...", "tool_call_id": "..."}]
    """
    payload = _decode_jsonish(raw)
    if isinstance(payload, str):
        payload = _decode_jsonish(payload)
    found: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            skill = str(node.get("skill_name") or "").strip()
            if _is_valid_skill_name(skill) and skill not in found:
                found.append(skill)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return found


def _decode_mappingish(value: Any) -> Any:
    """Decode JSON or Python-literal mappings commonly embedded in tool output."""
    decoded = _decode_jsonish(value)
    if not isinstance(decoded, str):
        return decoded
    text = decoded.strip()
    if not text or text[0] not in "{[":
        return decoded
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return decoded


def _tool_output_data_mapping(raw: Any) -> dict[str, Any] | None:
    """Pull the ``data={...}`` mapping from skill_tool output when present."""
    payload = _decode_mappingish(raw)
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        return None
    nested = _decode_mappingish(payload)
    if isinstance(nested, dict):
        return nested
    # ``success=True data={'skill_directory': '...', 'skill_content': '...'}``
    data_idx = payload.find("data=")
    if data_idx < 0:
        return None
    rest = payload[data_idx + len("data="):].strip()
    # skill_content often contains newlines/quotes that break literal_eval on the
    # whole mapping. Prefer a targeted skill_directory slice, then content name.
    nested = _decode_mappingish(rest)
    if isinstance(nested, dict):
        return nested
    return {"_raw_output": rest}


def _skills_from_tool_output(raw: Any) -> list[str]:
    """Extract skill identities from skill_tool output.

    Prefer the basename under ``skills/`` (EvolutionStore directory identity,
    e.g. ``beer`` / ``tianqi``). Frontmatter / ``skill_name`` display titles are
    secondary fallbacks only. Never return polluted slices of skill_content.
    """
    found: list[str] = []

    def _add(skill: str | None) -> None:
        if skill and skill not in found:
            found.append(skill)

    data = _tool_output_data_mapping(raw)
    if isinstance(data, dict):
        # Directory slug first — matches skills/<dir> and EvolutionStore.
        for key in _SKILL_PATH_KEYS:
            value = data.get(key)
            if isinstance(value, str):
                _add(_skill_from_directory_path(value))
        raw_tail = data.get("_raw_output")
        if isinstance(raw_tail, str):
            _add(_skill_from_directory_path(raw_tail))
        # Display / frontmatter names only after directory identity.
        _add(str(data.get("skill_name") or "").strip() or None)
        content = data.get("skill_content")
        if isinstance(content, str):
            _add(_skill_from_skill_content(content))
        if isinstance(raw_tail, str):
            _add(_skill_from_skill_content(raw_tail))
        if found:
            return found

    # Last resort: agent-core style ``skills/<name>`` string scan on the raw text.
    text = str(raw or "")
    for sep in ("skills\\", "skills/"):
        idx = text.find(sep)
        if idx < 0:
            continue
        rest = text[idx + len(sep):]
        skill = rest.split("'")[0].split('"')[0].split(",")[0].split("\\")[0].split("/")[0].strip()
        _add(skill if _is_valid_skill_name(skill) else None)
    _add(_skill_from_skill_content(text))
    return found


def _prefer_evolution_store_skill_names(candidates: list[str]) -> list[str]:
    """Keep only EvolutionStore-safe ``skills/<dir>`` slugs.

    Display titles like ``天气`` or ``Beer — Styles...`` are never used as the
    sleep skill identity — ``EvolutionStore.create_skill`` only accepts
    ``^[a-zA-Z0-9_-]+$``.
    """
    return [
        s
        for s in dict.fromkeys(candidates)
        if s and _is_evolution_store_skill_name(s)
    ]


def _tool_events(spans: list[dict[str, Any]]) -> list[tuple[int, str, list[str]]]:
    """Return ``(start_time, tool_name, skills)`` for every ``tool.*`` span, in order."""
    events: list[tuple[int, str, list[str]]] = []
    for span in spans:
        name = str(span.get("name") or "").strip()
        if not name.startswith("tool."):
            continue
        tool_name = name[len("tool."):]
        if not tool_name:
            continue
        attrs = _span_attrs(span)
        candidates: list[str] = []
        if tool_name in {"skill_tool", "skill", "load_skill", "use_skill"}:
            # Always read tool output so skills/<dir> slugs (beer, tianqi) win over
            # frontmatter / tool-input display names that EvolutionStore rejects.
            out = attrs.get(semconv.GEN_AI_TOOL_OUTPUT)
            if out is None:
                out = attrs.get(semconv.LANGFUSE_OBSERVATION_OUTPUT)
            candidates.extend(_skills_from_tool_output(out))
            candidates.extend(_skills_from_tool_input(attrs.get(semconv.GEN_AI_TOOL_INPUT)))
            candidates.extend(_skills_from_tool_input(attrs.get(semconv.LANGFUSE_OBSERVATION_INPUT)))
            candidates = _prefer_evolution_store_skill_names(candidates)
        skills = [s for s in dict.fromkeys(candidates) if s]
        events.append((_start_time(span), tool_name, skills))
    return events


def _collect_tools_and_skills(spans: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    tools: list[str] = []
    skills: list[str] = []
    for _start, tool_name, span_skills in _tool_events(spans):
        if tool_name not in tools:
            tools.append(tool_name)
        for skill in span_skills:
            if skill not in skills:
                skills.append(skill)
    return tools, skills


def _tool_turns_by_user_index(
    spans: list[dict[str, Any]],
    n_user_turns: int,
) -> dict[int, list[dict[str, Any]]]:
    """Attach each ``tool.*`` span to the user turn it most likely served.

    The prompt of the latest ``llm.call`` span that started before the tool
    span tells how many user turns had been seen at that point; the tool call
    therefore belongs to the last of those turns. Spans without usable timing
    fall back to the last user turn.
    """
    llm_marks: list[tuple[int, int]] = []
    for span in spans:
        if str(span.get("name") or "").strip() != "llm.call":
            continue
        prompt = _prompt_messages(_span_attrs(span))
        n_users = sum(
            1
            for m in prompt
            if str(m.get("role") or "") == "user" and _unwrap_user_content(str(m.get("content") or ""))
        )
        llm_marks.append((_start_time(span), n_users))
    llm_marks.sort()

    out: dict[int, list[dict[str, Any]]] = {}
    last_index = max(n_user_turns - 1, 0)
    for start, tool_name, skills in _tool_events(spans):
        seen_users = 0
        if start:
            for mark_start, n_users in llm_marks:
                if mark_start <= start:
                    seen_users = max(seen_users, n_users)
                else:
                    break
        index = min(seen_users - 1, last_index) if seen_users > 0 else last_index
        out.setdefault(max(index, 0), []).append({"role": "tool", "content": tool_name, "skills": list(skills)})
    return out


def _digest_from_trace(
    trace_id: str,
    spans: list[dict[str, Any]],
    *,
    project: str,
) -> SessionDigest | None:
    """Build one SessionDigest from spans that share an OTLP ``traceId``."""
    if not trace_id or _spans_look_like_noise(spans):
        return None

    spans = sorted(spans, key=_start_time)
    main = _select_main_llm_span(spans)
    if main is None:
        return None

    attrs = _span_attrs(main)
    completion = list(_completion_messages(attrs))
    messages = list(_prompt_messages(attrs)) + completion

    # If main call has no completion, try earlier llm.call completions for assistant text.
    if not completion:
        for span in reversed(spans):
            if str(span.get("name") or "").strip() != "llm.call":
                continue
            if span is main:
                continue
            extra = _completion_messages(_span_attrs(span))
            if extra:
                messages.extend(extra)
                break

    user_prompts: list[str] = []
    assistant_finals: list[str] = []
    ordered: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "")
        if role == "user":
            cleaned = _unwrap_user_content(content)
            if cleaned and not (user_prompts and user_prompts[-1] == cleaned):
                user_prompts.append(cleaned)
                ordered.append({"role": "user", "content": cleaned, "skills": []})
        elif role == "assistant":
            text = _strip_system_reminder(content).strip()
            if text and not (assistant_finals and assistant_finals[-1] == text):
                assistant_finals.append(text)
                ordered.append({"role": "assistant", "content": text, "skills": []})

    if not user_prompts:
        return None

    tools_used, skills_used = _collect_tools_and_skills(spans)
    tool_turns = _tool_turns_by_user_index(spans, len(user_prompts))
    turns: list[dict[str, Any]] = []
    user_index = -1
    for turn in ordered:
        turns.append(turn)
        if turn["role"] == "user":
            user_index += 1
            turns.extend(tool_turns.get(user_index, []))

    return SessionDigest(
        trace_id=trace_id,
        project=project,
        user_prompts=user_prompts,
        assistant_finals=assistant_finals,
        tools_used=tools_used,
        skills_used=skills_used,
        feedback_signals=[],
        n_user_turns=len(user_prompts),
        n_assistant_turns=len(assistant_finals),
        turns=turns,
    )


def load_jiuwenswarm_session_digests(
    path: str | Path,
    *,
    project: str = "invoked",
    trace_id: str | None = None,
    max_sessions: int = 0,
) -> list[SessionDigest]:
    """Group ``traces-*.jsonl`` spans by OTLP ``traceId`` and emit SessionDigest rows.

    Each ``traceId`` is one complete conversation trajectory stored in
    ``SessionDigest.trace_id``. Optional ``trace_id`` filters to one trajectory.
    """
    root = Path(path).expanduser()
    files = _trace_files(root)
    if not files:
        return []

    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for file_path in files:
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                for span in _iter_spans_from_record(record):
                    tid = _trace_id_from_span(span)
                    if not tid:
                        continue
                    if trace_id is not None and tid != trace_id:
                        continue
                    by_trace[tid].append(span)

    digests: list[SessionDigest] = []
    # Prefer recently active traces when max_sessions truncates.
    ordered_traces = sorted(
        by_trace.items(),
        key=lambda item: max((_start_time(span) for span in item[1]), default=0),
        reverse=True,
    )
    for tid, spans in ordered_traces:
        digest = _digest_from_trace(tid, spans, project=project)
        if digest is not None:
            digests.append(digest)
        if max_sessions > 0 and len(digests) >= max_sessions:
            break
    return digests


__all__ = [
    "has_jiuwenswarm_traces",
    "load_jiuwenswarm_session_digests",
]
