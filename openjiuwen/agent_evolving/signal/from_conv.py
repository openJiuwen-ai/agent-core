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
from openjiuwen.agent_evolving.utils import TuneUtils
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    trajectory_steps,
)
from openjiuwen.core.common.logging import logger


def _get_field(obj: object, key: str, default: object = "") -> object:
    """Read a field from a dict or object uniformly."""
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _message_dedup_key(msg: object) -> Tuple[str, str, str, str, str]:
    """Stable identity for trajectory message deduplication."""
    tool_calls = _get_field(msg, "tool_calls", []) or []
    try:
        tool_calls_repr = json.dumps(tool_calls, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        tool_calls_repr = str(tool_calls)
    return (
        str(_get_field(msg, "role") or ""),
        str(_get_field(msg, "content") or ""),
        str(_get_field(msg, "tool_call_id") or ""),
        str(_get_field(msg, "name") or ""),
        tool_calls_repr,
    )


def _normalize_message(msg: object) -> dict:
    """Normalize runtime message objects into message-like dicts."""
    if isinstance(msg, dict):
        return msg
    normalized: dict = {
        "role": str(_get_field(msg, "role") or ""),
        "content": _get_field(msg, "content") or "",
    }
    name = _get_field(msg, "name", None)
    if name not in ("", None):
        normalized["name"] = name
    tool_call_id = _get_field(msg, "tool_call_id", None)
    if tool_call_id not in ("", None):
        normalized["tool_call_id"] = tool_call_id
    tool_calls = _get_field(msg, "tool_calls", None)
    if tool_calls:
        normalized["tool_calls"] = tool_calls
    return normalized


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
_TOOL_SCHEMA_PATTERN = re.compile(r"\{'content': '---\\nname: [^\n]+\\ndescription:")
_USER_FEEDBACK_PROMPT_CN = (
    "判断以下用户消息是否包含对对话中已使用 skill 的被动纠正或可沉淀的改进反馈。\n"
    "只有当用户消息明确指出 agent 的理解、步骤、顺序、输出内容或工具使用需要调整时，"
    "才认为值得转成演进信号。\n"
    "若用户一次反馈涉及多个 skill，请按 skill 拆成多条；每条只写与该 skill 相关的 excerpt。\n"
    "不要把无关 skill 硬塞进 items。\n\n"
    "候选 skill（本轮对话中出现过）：{skill_names}\n"
    "最近用户消息：{user_messages}\n\n"
    "输出 JSON（二选一）：\n"
    '1) 多 skill: {{"is_feedback": true/false, "items": [{{"skill_name": "str", "excerpt": "str"}}]}}\n'
    '2) 兼容单条: {{"is_feedback": true/false, "excerpt": "str", "skill_name": "str可选"}}\n'
)
_USER_FEEDBACK_PROMPT_EN = (
    "Determine whether the following user messages contain passive corrective feedback "
    "or reusable improvement guidance for skills used in this conversation.\n"
    "Only treat the messages as evolution signals when the user is clearly correcting "
    "the agent's understanding, ordering, steps, output content, or tool usage.\n"
    "If one user message covers multiple skills, split into multiple items; "
    "each excerpt must relate only to that skill. Do not force unrelated skills.\n\n"
    "Candidate skills (seen in this conversation): {skill_names}\n"
    "Recent user messages: {user_messages}\n\n"
    "Output JSON (either form):\n"
    '1) Multi-skill: {{"is_feedback": true/false, "items": [{{"skill_name": "str", "excerpt": "str"}}]}}\n'
    '2) Legacy single: {{"is_feedback": true/false, "excerpt": "str", "skill_name": "str optional"}}\n'
)


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

    # Legacy single-object form
    excerpt = str(parsed.get("excerpt") or default_excerpt).strip()
    if not excerpt:
        return []
    skill = str(parsed.get("skill_name") or "").strip()
    if skill and skill in allowed:
        return [(skill, excerpt[:600])]
    if len(candidate_skills) == 1:
        return [(default_skill, excerpt[:600])]
    # Multiple candidates but no per-skill split: one signal per candidate
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
        return self._deduplicate([signal for signal in signals if signal.signal_type in enabled_signal_types])

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
        user_messages = [
            str(_get_field(msg, "content")).strip()
            for msg in messages
            if str(_get_field(msg, "role")) == "user" and str(_get_field(msg, "content")).strip()
        ][-5:]
        if not user_messages:
            return []

        traj_skills = self.collect_skills_from_messages(messages)
        extra = [str(s).strip() for s in (extra_skills or []) if str(s).strip()]
        skill_names = list(dict.fromkeys([*traj_skills, *extra]))
        logger.info(
            "[detect_user_intent] skills from messages = %s extra=%s merged=%s",
            traj_skills,
            extra,
            skill_names,
        )
        if not skill_names:
            return []

        if self._llm is None or not self._model:
            return self._fallback_user_feedback_signals(user_messages, skill_names)

        prompt_template = _USER_FEEDBACK_PROMPT_CN if self._language == "cn" else _USER_FEEDBACK_PROMPT_EN
        prompt = prompt_template.format(
            skill_names=", ".join(skill_names),
            user_messages="\n".join(user_messages)[:2000],
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
            return self._fallback_user_feedback_signals(user_messages, skill_names)

        parsed = _parse_llm_feedback_response(raw)
        if parsed is None:
            return self._fallback_user_feedback_signals(user_messages, skill_names)

        if isinstance(parsed, dict) and not parsed.get("is_feedback", True) and "items" not in parsed:
            return []

        pairs = _normalize_feedback_items(
            parsed,
            candidate_skills=skill_names,
            default_excerpt=user_messages[-1],
        )
        if not pairs:
            return self._fallback_user_feedback_signals(user_messages, skill_names)

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

        Later LLM steps often replay the full chat history. Messages already
        present (by role/content/tool identity) are skipped to avoid duplicates.

        Args:
            trajectory: Trajectory object to convert.

        Returns:
            List of message dicts compatible with signal detection logic.
        """
        messages: List[dict] = []
        seen_keys: Set[Tuple[str, str, str, str, str]] = set()
        tool_call_id_to_name: Dict[str, str] = {}

        def _append_message(msg: object) -> None:
            key = _message_dedup_key(msg)
            if key in seen_keys:
                return
            seen_keys.add(key)
            messages.append(_normalize_message(msg))
            tool_calls = _get_field(msg, "tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    tc_id = _get_field(tc, "id", "")
                    tc_name = _get_field(tc, "name", "")
                    if tc_id and tc_name:
                        tool_call_id_to_name[tc_id] = tc_name

        for step in trajectory_steps(trajectory):
            if step.kind == "llm" and isinstance(step.detail, LLMCallDetail):
                for msg in step.detail.messages:
                    _append_message(msg)

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

                _append_message(tool_msg)

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
                    tc_id = str(_get_field(tc, "id"))
                    tc_name = str(_get_field(tc, "name"))
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

                active_skill = self._resolve_active_skill(msg_idx, skill_read_history)

                if tool_call_id and tool_call_id in pending_scripts:
                    has_failure = bool(_FAILURE_KEYWORDS.search(content)) if content else False
                    if not has_failure:
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

    def _detect_skill_from_tool_calls_with_source(
        self,
        tool_calls: list,
    ) -> Optional[Tuple[str, str]]:
        """Backward-compatible: return the first ``(skill_name, source)`` hit."""
        detected = self._detect_skills_from_tool_calls_with_source(tool_calls)
        return detected[0] if detected else None

    def _detect_skills_from_tool_calls_with_source(
        self,
        tool_calls: list,
    ) -> List[Tuple[str, str]]:
        """Return all ``(skill_name, source)`` hits from tool calls in order.

        source examples:
        - ``assistant.skill_tool args.skill_name``
        - ``assistant.skill_complete args.skill_name``
        - ``assistant.read_file path .../weather/SKILL.md``
        """
        results: List[Tuple[str, str]] = []
        seen: Set[str] = set()
        for tool_call in tool_calls:
            name = self._tool_call_name(tool_call).lower()
            arguments = self._tool_call_arguments(tool_call)
            skill_name: Optional[str] = None
            source = ""

            matched = _SKILL_MD_PATTERN.search(arguments)
            if matched and self._is_skill_md_read_tool(name):
                skill_name = matched.group(1)
                snippet = matched.group(0)
                source = f"assistant.tool={name or 'read'} path_match={snippet!r}"
            elif name in ("skill_tool", "skill_complete"):
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
                    logger.debug("[ConversationSignalDetector] failed to parse %s arguments: %s", name, exc)

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

    @staticmethod
    def _tool_call_name(tool_call: object) -> str:
        """Return tool name from flat or OpenAI-nested tool_call shapes."""
        name = str(_get_field(tool_call, "name") or "")
        if name:
            return name
        function = _get_field(tool_call, "function", None)
        if function is not None:
            return str(_get_field(function, "name") or "")
        return ""

    @staticmethod
    def _tool_call_arguments(tool_call: object) -> str:
        """Return tool arguments from flat or OpenAI-nested tool_call shapes."""
        arguments = _get_field(tool_call, "arguments", "")
        if arguments not in ("", None):
            return arguments if isinstance(arguments, str) else str(arguments)
        function = _get_field(tool_call, "function", None)
        if function is not None:
            nested = _get_field(function, "arguments", "")
            return nested if isinstance(nested, str) else str(nested or "")
        return ""

    @classmethod
    def _format_message_for_trace(cls, msg_idx: int, msg: object, content_limit: int = 240) -> str:
        """Compact one-line summary of a message for trajectory dump logs."""
        role = str(_get_field(msg, "role") or "")
        name = str(_get_field(msg, "name") or "")
        content = str(_get_field(msg, "content") or "").replace("\n", "\\n")
        if len(content) > content_limit:
            content = content[:content_limit] + "..."
        tool_calls = _get_field(msg, "tool_calls", []) or []
        tc_parts: List[str] = []
        for tc in tool_calls:
            tc_name = cls._tool_call_name(tc)
            tc_args = cls._tool_call_arguments(tc)
            if len(tc_args) > 180:
                tc_args = tc_args[:180] + "..."
            tc_parts.append(f"{tc_name}({tc_args})")
        parts = [f"msg[{msg_idx}]", f"role={role}"]
        if name:
            parts.append(f"name={name}")
        if tc_parts:
            parts.append(f"tool_calls=[{'; '.join(tc_parts)}]")
        if content:
            parts.append(f"content={content!r}")
        return " ".join(parts)

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
        if dump_trajectory:
            logger.info(
                "[ConversationSignalDetector._infer_skill_from_messages] "
                "trajectory dump begin count=%d",
                len(messages),
            )
            for msg_idx, msg in enumerate(messages):
                logger.info(
                    "[ConversationSignalDetector] trajectory %s",
                    self._format_message_for_trace(msg_idx, msg),
                )
            logger.info(
                "[ConversationSignalDetector._infer_skill_from_messages] "
                "trajectory dump end count=%d",
                len(messages),
            )

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
                    logger.info("[ConversationSignalDetector] skill hit: %s", detail)
            elif role in ("tool", "function"):
                content = str(_get_field(msg, "content") or "")
                matched = _SKILL_MD_PATTERN.search(content)
                if matched:
                    skill_name = matched.group(1)
                    if self._is_existing_skill(skill_name):
                        skill_read_history.append((msg_idx, skill_name))
                        snippet = matched.group(0)
                        detail = (
                            f"msg[{msg_idx}] role={role} skill={skill_name!r} "
                            f"via tool_result path_match={snippet!r}"
                        )
                        hit_details.append(detail)
                        logger.info("[ConversationSignalDetector] skill hit: %s", detail)
                # skill_complete / unload metadata in tool results
                if "unload_skill_name" in content:
                    unload_match = re.search(r"unload_skill_name['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", content)
                    if unload_match and self._is_existing_skill(unload_match.group(1)):
                        skill_name = unload_match.group(1)
                        skill_read_history.append((msg_idx, skill_name))
                        detail = (
                            f"msg[{msg_idx}] role={role} skill={skill_name!r} "
                            f"via tool_result unload_skill_name"
                        )
                        hit_details.append(detail)
                        logger.info("[ConversationSignalDetector] skill hit: %s", detail)
                elif "Skill '" in content and "marked as complete" in content:
                    complete_match = re.search(r"Skill '([^']+)' marked as complete", content)
                    if complete_match and self._is_existing_skill(complete_match.group(1)):
                        skill_name = complete_match.group(1)
                        skill_read_history.append((msg_idx, skill_name))
                        detail = (
                            f"msg[{msg_idx}] role={role} skill={skill_name!r} "
                            f"via tool_result skill_complete"
                        )
                        hit_details.append(detail)
                        logger.info("[ConversationSignalDetector] skill hit: %s", detail)
        return skill_read_history, hit_details

    def _infer_skill_from_messages(self, messages: List[dict]) -> Optional[str]:
        """Return the most recently active skill (backward-compatible single value)."""
        skill_read_history, hit_details = self._scan_skill_hits(messages, dump_trajectory=True)
        found_skills = [name for _, name in skill_read_history]
        unique_skills = list(dict.fromkeys(found_skills))
        active = self._resolve_active_skill(len(messages), skill_read_history)
        logger.info(
            "[ConversationSignalDetector._infer_skill_from_messages] "
            "found=%s unique=%s active=%r hits=%s",
            found_skills,
            unique_skills,
            active,
            hit_details,
        )
        return active

    def _fallback_user_feedback_signals(
        self,
        user_messages: List[str],
        skill_names: Union[str, List[str]],
    ) -> List[EvolutionSignal]:
        names = [skill_names] if isinstance(skill_names, str) else list(skill_names)
        names = [n for n in names if n]
        if not names:
            return []
        for message in reversed(user_messages):
            if _CORRECTION_PATTERN.search(message):
                return [self._make_user_feedback_signal(message, name) for name in names]
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
        raw_args = _get_field(tool_call, "arguments")
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
