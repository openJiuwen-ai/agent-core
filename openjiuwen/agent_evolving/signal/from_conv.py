# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ConversationSignalDetector converts Trajectory or messages to evolution signals."""

from __future__ import annotations

import json
import re
import warnings
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from openjiuwen.agent_evolving.protocols import USER_INTENT_SIGNAL
from openjiuwen.agent_evolving.signal.base import (
    EvolutionSignal,
    make_evolution_signal,
    make_signal_fingerprint,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    trajectory_steps,
)
from openjiuwen.agent_evolving.utils import TuneUtils
from openjiuwen.core.common.logging import logger


def _get_field(obj: object, key: str, default: object = "") -> object:
    """Read a field from a dict or object uniformly."""
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _tool_call_field(tool_call: object, field: str) -> object:
    """Read direct or OpenAI ``function``-nested tool-call fields."""

    value = _get_field(tool_call, field, "")
    if value:
        return value
    function = _get_field(tool_call, "function", None)
    return _get_field(function, field, "") if function is not None else ""


def _extract_around_match(
    content: str,
    match: re.Match,
    before: int = 300,
    after: int = 300,
) -> str:
    """Return an excerpt around matched position."""
    start = max(0, match.start() - before)
    end = min(len(content), match.end() + after)
    return content[start:end]


def _response_to_text(response: object) -> str:
    """Convert common LLM response shapes to plain text."""
    if hasattr(response, "content"):
        return str(getattr(response, "content") or "")
    if isinstance(response, dict):
        return str(response.get("content", "") or response.get("text", "") or "")
    return str(response or "")


_FAILURE_KEYWORDS = re.compile(
    r"error(?!\s*=\s*None)|exception|traceback|failed|failure|timeout|timed out"
    r"|errno|connectionerror|oserror|valueerror|typeerror"
    r"|错误|异常|失败|超时"
    r"|no such file|permission denied|access denied"
    r"|command not found|not recognized"
    r"|module not found"
    r"|econnrefused|econnreset|enoent|enotfound"
    r"|npm err!",
    re.IGNORECASE,
)

_CORRECTION_PATTERNS = [
    r"不对[，,。!]?",
    r"不是[这那]",
    r"错[了啦]",
    r"应该(是|用|改|换)",
    r"你搞错[了啦]",
    r"这不对",
    r"重新(来|做|执行|尝试)",
    r"你理解错[了啦]",
    r"纠正一下",
    r"我的意思是",
    r"that('s| is) (wrong|incorrect|not right)",
    r"you'?re wrong",
    r"should (be|use|have)",
    r"actually[,，]",
    r"no[,，] (wait|actually)",
    r"correct(ion)?:",
    r"fix(ed)?:",
]
_CORRECTION_PATTERN = re.compile("|".join(_CORRECTION_PATTERNS), re.IGNORECASE)

_SKILL_MD_PATTERN = re.compile(r"[/\\]+([^/\\]+)[/\\]+SKILL\.md", re.IGNORECASE)
_SKILL_DIR_PATTERN = re.compile(r"[/\\]skills[/\\]([^/\\]+)(?:[/\\]|$)", re.IGNORECASE)
_SKILL_FRONTMATTER_NAME_PATTERN = re.compile(
    r"(?:^|\\n|\n)name:\s*([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
_TOOL_SCHEMA_PATTERN = re.compile(r"\{'content': '---\\nname: [^\n]+\\ndescription:")
_USER_FEEDBACK_MAX_TURNS = 9
_USER_FEEDBACK_CONTEXT_CHAR_LIMIT = 3000
_USER_FEEDBACK_LAST_USER_CHAR_LIMIT = 1000

_USER_FEEDBACK_PROMPT_CN = (
    "判断「待判定的用户消息」是否包含对对话中已使用 skill 的被动纠正或可沉淀的改进反馈。\n"
    "结合对话上下文中的助手回复理解用户在纠正什么；"
    "只有当【待判定的用户消息】明确指出 agent 的理解、步骤、顺序、输出内容或工具使用需要调整时，"
    "才认为值得转成演进信号。\n"
    "不要仅因更早的用户消息含纠正词就判定为反馈。\n"
    "若用户一次反馈涉及多个 skill，请按 skill 拆成多条；每条只写与该 skill 相关的 excerpt。\n"
    "不要把无关 skill 硬塞进 items。\n\n"
    "候选 skill（本轮对话中出现过）：{skill_names}\n\n"
    f"对话上下文（带角色标识，最近最多 {_USER_FEEDBACK_MAX_TURNS} 轮问答）：\n"
    "{conversation_context}\n\n"
    "待判定的用户消息：\n"
    "{last_user_message}\n\n"
    "输出 JSON（二选一）：\n"
    '1) 多 skill: {{"is_feedback": true/false, "items": [{{"skill_name": "str", "excerpt": "str"}}]}}\n'
    '2) 兼容单条: {{"is_feedback": true/false, "excerpt": "str", "skill_name": "str可选"}}\n'
)
_USER_FEEDBACK_PROMPT_EN = (
    "Determine whether the LAST user message (to judge) contains passive corrective feedback "
    "or reusable improvement guidance for skills used in this conversation.\n"
    "Use the labeled conversation context (including assistant replies) to understand what "
    "the user is correcting. Only treat it as an evolution signal when the LAST user message "
    "clearly corrects the agent's understanding, ordering, steps, output content, or tool usage.\n"
    "Do not treat earlier user messages alone as sufficient evidence of feedback.\n"
    "If one user message covers multiple skills, split into multiple items; "
    "each excerpt must relate only to that skill. Do not force unrelated skills.\n\n"
    "Candidate skills (seen in this conversation): {skill_names}\n\n"
    f"Conversation context (role-labeled, up to {_USER_FEEDBACK_MAX_TURNS} recent Q&A turns):\n"
    "{conversation_context}\n\n"
    "Last user message (to judge):\n"
    "{last_user_message}\n\n"
    "Output JSON (either form):\n"
    '1) Multi-skill: {{"is_feedback": true/false, "items": [{{"skill_name": "str", "excerpt": "str"}}]}}\n'
    '2) Legacy single: {{"is_feedback": true/false, "excerpt": "str", "skill_name": "str optional"}}\n'
)


def _extract_dialog_turns(messages: Sequence[object]) -> List[Tuple[str, str]]:
    """Return ``[(role, content), ...]`` for user/assistant messages with non-empty content."""
    turns: List[Tuple[str, str]] = []
    for msg in messages:
        role = str(_get_field(msg, "role") or "")
        if role not in ("user", "assistant"):
            continue
        content = str(_get_field(msg, "content") or "").strip()
        if content:
            turns.append((role, content))
    return turns


def _unwrap_channel_user_message(text: str) -> str:
    """Extract real user text from channel ``lead-in + JSON`` envelopes.

    Inbound wrappers look like ``你收到一条消息：\\n{"content": "...", ...}``.
    Feedback detection only needs the inner ``content``; envelope metadata
    (source/timezone/supplementary_info) is noise for intent classification.
    """
    stripped = (text or "").strip()
    if not stripped:
        return text
    head, sep, payload = stripped.partition("\n")
    payload = payload.strip()
    if not sep or head.lstrip().startswith("{") or not payload.startswith("{"):
        return text
    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError:
        return text
    if isinstance(envelope, dict):
        inner = envelope.get("content")
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    return text


def _is_feedback_context_noise(content: str) -> bool:
    """Return True for injected reminders / attachments with no real user text."""
    text = (content or "").strip()
    if not text:
        return True
    lower = text.lower()
    if "<system-reminder>" not in lower and "<prompt-attachment" not in lower:
        return False
    without = re.sub(
        r"<system-reminder>[\s\S]*?</system-reminder>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    without = re.sub(
        r"<prompt-attachment\b[^>]*>[\s\S]*?</prompt-attachment>",
        "",
        without,
        flags=re.IGNORECASE,
    ).strip()
    return not without


def _prepare_feedback_dialog_turns(
    messages: Sequence[object],
) -> List[Tuple[str, str]]:
    """Build cleaned user/assistant turns for feedback prompts.

    - Unwrap channel JSON envelopes for user turns
    - Drop system-reminder / prompt-attachment-only noise
    - Collapse consecutive duplicate (role, content) pairs
    """
    cleaned: List[Tuple[str, str]] = []
    for role, content in _extract_dialog_turns(messages):
        if role == "user":
            content = _unwrap_channel_user_message(content)
        content = str(content or "").strip()
        if not content or _is_feedback_context_noise(content):
            continue
        if cleaned and cleaned[-1][0] == role and cleaned[-1][1] == content:
            continue
        cleaned.append((role, content))
    return cleaned


def _format_feedback_dialog_line(role: str, content: str, *, language: str) -> str:
    if language == "cn":
        label = "用户" if role == "user" else "助手"
    else:
        label = "User" if role == "user" else "Assistant"
    return f"[{label}] {content}"


def _build_user_feedback_prompt_inputs(
    messages: Sequence[object],
    *,
    language: str,
    max_turns: int = _USER_FEEDBACK_MAX_TURNS,
) -> Optional[Tuple[str, str]]:
    """Build ``(conversation_context, last_user_message)`` for feedback detection."""
    dialog = _prepare_feedback_dialog_turns(messages)
    last_user_idx: Optional[int] = None
    for idx in range(len(dialog) - 1, -1, -1):
        if dialog[idx][0] == "user":
            last_user_idx = idx
            break
    if last_user_idx is None:
        return None

    last_user_message = dialog[last_user_idx][1][:_USER_FEEDBACK_LAST_USER_CHAR_LIMIT]

    user_seen = 0
    start = 0
    for idx in range(last_user_idx, -1, -1):
        if dialog[idx][0] == "user":
            user_seen += 1
            if user_seen >= max_turns:
                start = idx
                break

    context_lines = [
        _format_feedback_dialog_line(role, content, language=language)
        for role, content in dialog[start:last_user_idx]
    ]
    conversation_context = "\n".join(context_lines)[:_USER_FEEDBACK_CONTEXT_CHAR_LIMIT]
    if not conversation_context:
        conversation_context = "(无)" if language == "cn" else "(none)"
    return conversation_context, last_user_message


def _parse_llm_feedback_response(raw: str) -> Optional[object]:
    """Parse LLM feedback JSON (dict or list), tolerating markdown code fences."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = TuneUtils.parse_json_from_llm_response(text)
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def _normalize_feedback_items(
    parsed: object,
    *,
    candidate_skills: List[str],
    default_excerpt: str,
) -> List[Tuple[str, str]]:
    """Normalize LLM / legacy feedback JSON into ``[(skill_name, excerpt), ...]``."""
    if not candidate_skills:
        return []

    allowed = set(candidate_skills)
    default_skill = candidate_skills[-1]

    def _accept(skill: object, excerpt: object) -> Optional[Tuple[str, str]]:
        name = str(skill or "").strip()
        text = str(excerpt or "").strip() or default_excerpt
        if not name or name not in allowed or not text:
            return None
        return name, text[:600]

    items: List[Tuple[str, str]] = []
    if isinstance(parsed, list):
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            accepted = _accept(entry.get("skill_name"), entry.get("excerpt"))
            if accepted:
                items.append(accepted)
        return items

    if not isinstance(parsed, dict):
        return []

    if not parsed.get("is_feedback", True):
        return []

    raw_items = parsed.get("items")
    if isinstance(raw_items, list):
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            accepted = _accept(entry.get("skill_name"), entry.get("excerpt"))
            if accepted:
                items.append(accepted)
        if items:
            return items

    excerpt = str(parsed.get("excerpt") or default_excerpt).strip()
    if not excerpt:
        return []
    skill = str(parsed.get("skill_name") or "").strip()
    if skill and skill in allowed:
        return [(skill, excerpt[:600])]
    if len(candidate_skills) == 1:
        return [(default_skill, excerpt[:600])]
    return [(name, excerpt[:600]) for name in candidate_skills]

# Tools whose output is fetched content (web pages, files, search results).
_DATA_FETCH_TOOLS = frozenset(
    {
        "mcp_fetch_webpage",
        "fetch_webpage",
        "web_fetch",
        "search",
        "web_search",
        "google_search",
        "bing_search",
        "view_file",
        "read_file",
        "cat_file",
        "list_directory",
        "ls",
        "get_url",
        "curl",
        "wget",
    }
)

# Tools that execute inline code or shell commands.
_CODE_EXEC_TOOLS = frozenset(
    {
        "code",
        "bash",
        "execute_python_code",
        "run_python",
        "exec_code",
        "execute_code",
        "python_exec",
        "run_code",
    }
)

# Parameter keys where executable content (code or commands) can be found.
_EXEC_CONTENT_KEYS = (
    "code",
    "code_block",
    "script",
    "source",
    "python_code",
    "command",
    "cmd",
    "shell_command",
)

DetectionInput = Union[Trajectory, List[dict]]


class ConversationSignalDetector:
    """Extract evolution signals from Trajectory or message list.

    Migrated from online.SignalDetector, now accepts both Trajectory and List[dict].
    Unified interface for online and offline evolution paths.
    """

    def __init__(self, existing_skills: Optional[Set[str]] = None) -> None:
        """Initialize detector with optional existing skills set.

        Args:
            existing_skills: Set of skill names for skill_name resolution.
        """
        self._existing_skills = existing_skills or set()
        self._llm: object | None = None
        self._model = ""
        self._language = "cn"

    def detect(self, trajectory_or_messages: DetectionInput) -> List[EvolutionSignal]:
        """Detect deterministic evolution signals from Trajectory or messages."""
        return self._detect_message_signals(trajectory_or_messages)

    def _detect_message_signals(
        self,
        input_data: DetectionInput,
        *,
        signal_types: Optional[Set[str]] = None,
    ) -> List[EvolutionSignal]:
        try:
            messages = (
                self.convert_trajectory_to_messages(input_data)
                if isinstance(input_data, Trajectory)
                else list(input_data)
            )
            signals = self._detect_from_messages(messages)
        except Exception as exc:
            logger.warning(
                "[ConversationSignalDetector] message signal detection failed: %s",
                exc,
                exc_info=True,
            )
            return []
        enabled_signal_types = signal_types or {"execution_failure", "script_artifact"}
        deduped = self._deduplicate(
            [signal for signal in signals if signal.signal_type in enabled_signal_types]
        )
        for signal in deduped:
            tool_name = (signal.context or {}).get("tool_name")
            logger.info(
                "[ConversationSignalDetector] after_dedup tool attributed to skill=%s "
                "signal_type=%s tool=%s",
                signal.skill_name,
                signal.signal_type,
                tool_name,
            )
        return deduped

    def detect_trajectory_signals(
        self,
        trajectory: Optional[Trajectory],
        *,
        messages: Optional[List[dict]] = None,
        signal_types: Optional[Set[str]] = None,
    ) -> List[EvolutionSignal]:
        """Detect passive trajectory signals using deterministic conversation rules."""
        if messages is not None:
            input_data: DetectionInput = messages
        elif trajectory is not None:
            input_data = trajectory
        else:
            return []
        return self._detect_message_signals(
            input_data,
            signal_types=signal_types,
        )

    def bind_llm(
        self,
        *,
        llm: object,
        model: str,
        language: str = "cn",
    ) -> "ConversationSignalDetector":
        """Attach optional LLM context for passive user-message detection."""
        self._llm = llm
        self._model = model
        self._language = language
        return self

    async def detect_user_message_feedback(
        self,
        messages: List[dict],
    ) -> List[EvolutionSignal]:
        """Deprecated alias for detect_user_intent."""
        warnings.warn(
            "ConversationSignalDetector.detect_user_message_feedback() is deprecated; "
            "use detect_user_intent() and the user_intent signal type instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.detect_user_intent(messages)

    async def detect_user_intent(
        self,
        trajectory_or_messages: DetectionInput,
        *,
        extra_skills: Optional[Sequence[str]] = None,
    ) -> List[EvolutionSignal]:
        """Use LLM judgment to turn passive user messages into standard signals.

        May return multiple signals when the conversation used multiple skills and
        the user feedback covers more than one of them.

        Judgment is based on the **last** user message. Recent user/assistant turns
        (role-labeled, up to 9 Q&A) are provided as context only.

        Args:
            trajectory_or_messages: Trajectory or message list for this round.
            extra_skills: Session-scoped skills used earlier in the conversation
                (cross-turn inheritance when the current trajectory no longer
                contains skill_tool / skill_complete records).
        """
        messages = (
            self.convert_trajectory_to_messages(trajectory_or_messages)
            if isinstance(trajectory_or_messages, Trajectory)
            else list(trajectory_or_messages)
        )
        prompt_inputs = _build_user_feedback_prompt_inputs(
            messages,
            language=self._language,
        )
        if prompt_inputs is None:
            return []
        conversation_context, last_user_message = prompt_inputs
        logger.info(
            "[detect_user_intent] last_user_message=%s",
            last_user_message,
        )

        # Same candidate set as SkillEvolutionRail:
        #   session used skills=[...] (traj=[...])
        # Rail passes extra_skills=sorted(session_skills) which already is
        # session history ∪ current traj hits; prefer that list as skill_names.
        traj_skills = self.collect_skills_from_messages(messages)
        session_used_skills = [
            str(s).strip() for s in (extra_skills or []) if str(s).strip()
        ]
        skill_names = list(dict.fromkeys(session_used_skills or traj_skills))
        logger.info(
            "[detect_user_intent] session used skills=%s (traj=%s)",
            skill_names,
            traj_skills,
        )
        if not skill_names:
            return []

        if self._llm is None or not self._model:
            return self._fallback_user_feedback_signals(last_user_message, skill_names)

        prompt_template = _USER_FEEDBACK_PROMPT_CN if self._language == "cn" else _USER_FEEDBACK_PROMPT_EN
        prompt = prompt_template.format(
            skill_names=", ".join(skill_names),
            conversation_context=conversation_context,
            last_user_message=last_user_message,
        )

        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                timeout=30,
            )
            raw = _response_to_text(response)
        except Exception as exc:
            logger.warning("[ConversationSignalDetector] user feedback detection failed: %s", exc)
            return self._fallback_user_feedback_signals(last_user_message, skill_names)

        parsed = _parse_llm_feedback_response(raw)
        if parsed is None:
            return self._fallback_user_feedback_signals(last_user_message, skill_names)

        if isinstance(parsed, dict) and not parsed.get("is_feedback", True) and "items" not in parsed:
            return []

        pairs = _normalize_feedback_items(
            parsed,
            candidate_skills=skill_names,
            default_excerpt=last_user_message,
        )
        if not pairs:
            return self._fallback_user_feedback_signals(last_user_message, skill_names)

        return [
            self._make_user_feedback_signal(excerpt, skill_name)
            for skill_name, excerpt in pairs
        ]

    @staticmethod
    def convert_trajectory_to_messages(trajectory: Trajectory) -> List[dict]:
        """Convert trajectory steps (via ``trajectory_steps``) to message list format.

        The message format matches what SignalDetector.detect() expects:
        - LLM steps: messages from LLMCallDetail, including tool_calls
        - Tool steps: tool result from ToolCallDetail.call_result

        Args:
            trajectory: Trajectory object to convert.

        Returns:
            List of message dicts compatible with signal detection logic.
        """
        messages: List[dict] = []
        tool_call_id_to_name: Dict[str, str] = {}

        for step in trajectory_steps(trajectory):
            if step.kind == "llm" and isinstance(step.detail, LLMCallDetail):
                for msg in step.detail.messages:
                    messages.append(msg)
                    tool_calls = _get_field(msg, "tool_calls", [])
                    if tool_calls:
                        for tc in tool_calls:
                            tc_id = _tool_call_field(tc, "id")
                            tc_name = _tool_call_field(tc, "name")
                            if tc_id and tc_name:
                                tool_call_id_to_name[tc_id] = tc_name

                # Include the model response (assistant tool_calls live here, not only in inputs).
                response = step.detail.response
                resp_msg: Optional[dict] = None
                if isinstance(response, dict):
                    resp_msg = response
                elif response is not None:
                    resp_msg = {
                        "role": str(getattr(response, "role", "") or "assistant"),
                        "content": str(getattr(response, "content", "") or ""),
                    }
                    tool_calls = getattr(response, "tool_calls", None)
                    if tool_calls:
                        resp_msg["tool_calls"] = tool_calls
                if resp_msg:
                    has_payload = any(
                        resp_msg.get(key) for key in ("role", "content", "tool_calls")
                    )
                    if has_payload:
                        messages.append(resp_msg)
                        for tc in resp_msg.get("tool_calls") or []:
                            tc_id = _tool_call_field(tc, "id")
                            tc_name = _tool_call_field(tc, "name")
                            if tc_id and tc_name:
                                tool_call_id_to_name[tc_id] = tc_name

            elif step.kind == "tool" and isinstance(step.detail, ToolCallDetail):
                tool_name = step.detail.tool_name
                tool_call_id = step.detail.tool_call_id or step.meta.get("tool_call_id", "")

                if not tool_name and tool_call_id:
                    tool_name = tool_call_id_to_name.get(tool_call_id, "")

                result_content = ""
                if step.detail.call_result is not None:
                    result_content = str(step.detail.call_result)

                tool_msg = {
                    "role": "tool",
                    "content": result_content,
                }
                if tool_call_id:
                    tool_msg["tool_call_id"] = tool_call_id
                if tool_name:
                    tool_msg["name"] = tool_name

                messages.append(tool_msg)

        return messages

    def _detect_from_messages(self, messages: List[dict]) -> List[EvolutionSignal]:
        """Scan messages and return deduplicated signals.

        Original SignalDetector.detect() logic, moved here for unified handling.
        """
        signals: List[EvolutionSignal] = []
        skill_read_history: List[Tuple[int, str]] = []
        pending_scripts: Dict[str, str] = {}
        tool_call_id_to_name: Dict[str, str] = {}

        for msg_idx, msg in enumerate(messages):
            role = str(_get_field(msg, "role"))
            content = str(_get_field(msg, "content"))
            tool_calls = _get_field(msg, "tool_calls", [])

            if role == "assistant" and tool_calls:
                for skill_name in self._detect_skills_from_tool_calls(tool_calls):
                    skill_read_history.append((msg_idx, skill_name))

                for tc in tool_calls:
                    tc_id = str(_tool_call_field(tc, "id"))
                    tc_name = str(_tool_call_field(tc, "name"))
                    if tc_id and tc_name:
                        tool_call_id_to_name[tc_id] = tc_name
                    if tc_name.lower() in _CODE_EXEC_TOOLS:
                        code = self._extract_code_from_args(tc)
                        if code and tc_id:
                            pending_scripts[tc_id] = code

            if role in ("tool", "function"):
                tool_name = _get_field(msg, "name") or _get_field(msg, "tool_name") or ""
                tool_call_id = _get_field(msg, "tool_call_id", "")
                if not tool_name and tool_call_id:
                    tool_name = tool_call_id_to_name.get(tool_call_id, "")

                for skill_name, _source in self._extract_skills_from_tool_result(
                    content,
                    tool_name=str(tool_name or ""),
                ):
                    skill_read_history.append((msg_idx, skill_name))

                active_skill = self._resolve_active_skill(msg_idx, skill_read_history)

                if tool_call_id and tool_call_id in pending_scripts:
                    has_failure = bool(_FAILURE_KEYWORDS.search(content)) if content else False
                    if not has_failure:
                        logger.debug(
                            "[ConversationSignalDetector] tool attributed to skill=%s "
                            "signal_type=script_artifact tool=%s msg_idx=%d",
                            active_skill,
                            tool_name or None,
                            msg_idx,
                        )
                        signals.append(
                            make_evolution_signal(
                                signal_type="script_artifact",
                                section="Scripts",
                                excerpt=pending_scripts[tool_call_id][:600],
                                tool_name=tool_name,
                                skill_name=active_skill,
                                source="passive_conversation",
                            )
                        )
                    del pending_scripts[tool_call_id]

                if tool_name.lower() in _DATA_FETCH_TOOLS:
                    continue

                match = _FAILURE_KEYWORDS.search(content)
                if match:
                    if _TOOL_SCHEMA_PATTERN.search(content):
                        continue
                    excerpt = _extract_around_match(content, match)
                    logger.debug(
                        "[ConversationSignalDetector] tool attributed to skill=%s "
                        "signal_type=execution_failure tool=%s msg_idx=%d",
                        active_skill,
                        tool_name or None,
                        msg_idx,
                    )
                    signals.append(
                        make_evolution_signal(
                            signal_type="execution_failure",
                            section="Troubleshooting",
                            excerpt=excerpt,
                            tool_name=tool_name or None,
                            skill_name=active_skill,
                            source="passive_conversation",
                        )
                    )
        return signals

    @staticmethod
    def _resolve_active_skill(
        msg_idx: int,
        skill_read_history: List[Tuple[int, str]],
    ) -> Optional[str]:
        """Return the most recently read skill at or before *msg_idx*."""
        for idx, name in reversed(skill_read_history):
            if idx <= msg_idx:
                return name
        return None

    def _detect_skill_from_tool_calls(self, tool_calls: list) -> Optional[str]:
        """Return first skill name if any tool call loads a skill, else None."""
        detected = self._detect_skills_from_tool_calls_with_source(tool_calls)
        return detected[0][0] if detected else None

    def _detect_skills_from_tool_calls(self, tool_calls: list) -> List[str]:
        """Return all skill names loaded by tool calls in this assistant message."""
        return [name for name, _ in self._detect_skills_from_tool_calls_with_source(tool_calls)]

    def _detect_skills_from_tool_calls_with_source(
        self,
        tool_calls: list,
    ) -> List[Tuple[str, str]]:
        """Return all ``(skill_name, source)`` hits from tool calls in order."""
        results: List[Tuple[str, str]] = []
        seen: Set[str] = set()
        for tool_call in tool_calls:
            name = str(_tool_call_field(tool_call, "name") or "").lower()
            arguments = _tool_call_field(tool_call, "arguments")
            arguments = arguments if isinstance(arguments, str) else str(arguments or "")
            skill_name: Optional[str] = None
            source = ""

            matched = _SKILL_MD_PATTERN.search(arguments)
            if matched and self._is_skill_md_read_tool(name):
                skill_name = matched.group(1)
                snippet = matched.group(0)
                source = f"assistant.tool={name or 'read'} path_match={snippet!r}"
            elif name in ("skill_tool", "skill_complete") or name.endswith(".skill_tool"):
                try:
                    args_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
                    if isinstance(args_dict, dict):
                        skill_name = args_dict.get("skill_name")
                        rel = args_dict.get("relative_file_path")
                        source = (
                            f"assistant.{name} args.skill_name={skill_name!r}"
                            f" relative_file_path={rel!r}"
                        )
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.debug(
                        "[ConversationSignalDetector] failed to parse %s arguments: %s",
                        name,
                        exc,
                    )

            if not skill_name or not self._is_existing_skill(skill_name):
                continue
            key = str(skill_name)
            if key in seen:
                continue
            seen.add(key)
            results.append((key, source))
        return results

    def _is_existing_skill(self, skill_name: str) -> bool:
        return not self._existing_skills or skill_name in self._existing_skills

    @staticmethod
    def _is_skill_md_read_tool(name: str) -> bool:
        return not name or any(token in name for token in ("file", "read"))

    def collect_skills_from_messages(self, messages: List[dict]) -> List[str]:
        """Collect unique skill names used in the conversation (order preserved)."""
        skill_read_history, _hit_details = self._scan_skill_hits(messages)
        return list(dict.fromkeys(name for _, name in skill_read_history))

    def _scan_skill_hits(
        self,
        messages: List[dict],
        *,
        dump_trajectory: bool = False,
    ) -> Tuple[List[Tuple[int, str]], List[str]]:
        """Scan messages for skill loads; return history and human-readable hit details."""
        skill_read_history: List[Tuple[int, str]] = []
        hit_details: List[str] = []
        for msg_idx, msg in enumerate(messages):
            role = str(_get_field(msg, "role"))
            tool_calls = _get_field(msg, "tool_calls", [])
            if role == "assistant" and tool_calls:
                for skill_name, source in self._detect_skills_from_tool_calls_with_source(tool_calls):
                    skill_read_history.append((msg_idx, skill_name))
                    detail = f"msg[{msg_idx}] role=assistant skill={skill_name!r} via {source}"
                    hit_details.append(detail)
                    if dump_trajectory:
                        logger.info("[ConversationSignalDetector] skill hit: %s", detail)
            elif role in ("tool", "function"):
                content = str(_get_field(msg, "content") or "")
                tool_name = str(
                    _get_field(msg, "name") or _get_field(msg, "tool_name") or ""
                )
                for skill_name, source in self._extract_skills_from_tool_result(
                    content,
                    tool_name=tool_name,
                ):
                    skill_read_history.append((msg_idx, skill_name))
                    detail = (
                        f"msg[{msg_idx}] role={role} skill={skill_name!r} "
                        f"via tool_result {source}"
                    )
                    hit_details.append(detail)
                    if dump_trajectory:
                        logger.info("[ConversationSignalDetector] skill hit: %s", detail)
        return skill_read_history, hit_details

    def _extract_skills_from_tool_result(
        self,
        content: str,
        *,
        tool_name: str = "",
    ) -> List[Tuple[str, str]]:
        """Extract ``(skill_name, source)`` hits from a tool/function result payload."""
        results: List[Tuple[str, str]] = []
        seen: Set[str] = set()

        def _add(skill_name: Optional[str], source: str) -> None:
            name = str(skill_name or "").strip()
            if not name or name in seen or not self._is_existing_skill(name):
                return
            seen.add(name)
            results.append((name, source))

        matched = _SKILL_MD_PATTERN.search(content)
        if matched:
            _add(matched.group(1), f"path_match={matched.group(0)!r}")

        dir_matched = _SKILL_DIR_PATTERN.search(content)
        if dir_matched:
            _add(dir_matched.group(1), f"skills_dir={dir_matched.group(0)!r}")

        name_l = str(tool_name or "").lower()
        if name_l in ("skill_tool", "skill_complete") or name_l.endswith(".skill_tool"):
            fm = _SKILL_FRONTMATTER_NAME_PATTERN.search(content)
            if fm:
                _add(fm.group(1), "skill_tool_frontmatter_name")

        if "unload_skill_name" in content:
            unload_match = re.search(
                r"unload_skill_name['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]",
                content,
            )
            if unload_match:
                _add(unload_match.group(1), "unload_skill_name")

        if "Skill '" in content and "marked as complete" in content:
            complete_match = re.search(r"Skill '([^']+)' marked as complete", content)
            if complete_match:
                _add(complete_match.group(1), "skill_complete")

        return results

    def _infer_skill_from_messages(self, messages: List[dict]) -> Optional[str]:
        """Return the most recently active skill (backward-compatible single value)."""
        skill_read_history, _hit_details = self._scan_skill_hits(messages)
        return self._resolve_active_skill(len(messages), skill_read_history)

    def _fallback_user_feedback_signals(
        self,
        last_user_message: str,
        skill_names: Union[str, List[str]],
    ) -> List[EvolutionSignal]:
        """Rule fallback: only the last user message may trigger correction signals."""
        names = [skill_names] if isinstance(skill_names, str) else list(skill_names)
        names = [n for n in names if n]
        text = str(last_user_message or "").strip()
        if not names or not text:
            return []
        if _CORRECTION_PATTERN.search(text):
            return [self._make_user_feedback_signal(text, name) for name in names]
        return []

    @staticmethod
    def _make_user_feedback_signal(excerpt: str, skill_name: str) -> EvolutionSignal:
        return make_evolution_signal(
            signal_type=USER_INTENT_SIGNAL,
            section="Instructions",
            excerpt=excerpt[:600],
            skill_name=skill_name,
            source="passive_conversation",
        )

    @staticmethod
    def _extract_code_from_args(tool_call: object) -> str:
        """Extract inline code or command content from a code-execution tool call."""
        raw_args = _tool_call_field(tool_call, "arguments")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except ValueError:
                return ""
        if not isinstance(raw_args, dict):
            return ""
        for key in _EXEC_CONTENT_KEYS:
            value = raw_args.get(key, "")
            if isinstance(value, str) and len(value.strip()) > 20:
                return value
        return ""

    @staticmethod
    def _deduplicate(signals: List[EvolutionSignal]) -> List[EvolutionSignal]:
        """Deduplicate by (type, context.tool_name, skill_name, excerpt[:200])."""
        seen: set[tuple] = set()
        deduped: List[EvolutionSignal] = []
        for signal in signals:
            key = make_signal_fingerprint(signal)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(signal)
        return deduped


# Alias for backward compatibility
SignalDetector = ConversationSignalDetector


__all__ = [
    "ConversationSignalDetector",
    "SignalDetector",  # backward compatibility alias
    "make_signal_fingerprint",
]
