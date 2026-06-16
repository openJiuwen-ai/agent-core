# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ConversationSignalDetector converts Trajectory or messages to evolution signals."""

from __future__ import annotations

import json
import re
import warnings
from typing import Dict, List, Optional, Set, Tuple, Union

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
)
from openjiuwen.core.common.logging import logger


def _get_field(obj: object, key: str, default: object = "") -> object:
    """Read a field from a dict or object uniformly."""
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


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
    "判断以下用户消息是否包含对当前 skill 的被动纠正或可沉淀的改进反馈。\n"
    "只有当用户消息明确指出 agent 的理解、步骤、顺序或工具使用需要调整时，"
    "才认为值得转成演进信号。\n\n"
    "当前 skill：{skill_name}\n"
    "最近用户消息：{user_messages}\n\n"
    '输出 JSON: {{"is_feedback": true/false, "excerpt": "str"}}\n'
)
_USER_FEEDBACK_PROMPT_EN = (
    "Determine whether the following user messages contain passive corrective feedback "
    "or reusable improvement guidance for the current skill.\n"
    "Only treat the messages as an evolution signal when the user is clearly correcting "
    "the agent's understanding, ordering, steps, or tool usage.\n\n"
    "Current skill: {skill_name}\n"
    "Recent user messages: {user_messages}\n\n"
    'Output JSON: {{"is_feedback": true/false, "excerpt": "str"}}\n'
)

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
        messages: List[dict],
    ) -> List[EvolutionSignal]:
        """Use LLM judgment to turn passive user messages into standard signals."""
        if isinstance(messages, Trajectory):
            raise TypeError(
                "detect_user_intent() expects normalized messages; call convert_trajectory_to_messages() first."
            )
        user_messages = [
            str(_get_field(msg, "content")).strip()
            for msg in messages
            if str(_get_field(msg, "role")) == "user" and str(_get_field(msg, "content")).strip()
        ][-5:]
        if not user_messages:
            return []

        skill_name = self._infer_skill_from_messages(messages)
        if not skill_name:
            return []

        if self._llm is None or not self._model:
            return self._fallback_user_feedback_signals(user_messages, skill_name)

        prompt_template = _USER_FEEDBACK_PROMPT_CN if self._language == "cn" else _USER_FEEDBACK_PROMPT_EN
        prompt = prompt_template.format(
            skill_name=skill_name,
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
            return self._fallback_user_feedback_signals(user_messages, skill_name)

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return self._fallback_user_feedback_signals(user_messages, skill_name)
        if not isinstance(parsed, dict):
            return self._fallback_user_feedback_signals(user_messages, skill_name)

        if not parsed.get("is_feedback"):
            return []

        excerpt = str(parsed.get("excerpt") or user_messages[-1]).strip()
        return [self._make_user_feedback_signal(excerpt, skill_name)]

    @staticmethod
    def convert_trajectory_to_messages(trajectory: Trajectory) -> List[dict]:
        """Convert Trajectory.steps to message list format.

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

        for step in trajectory.steps:
            if step.kind == "llm" and isinstance(step.detail, LLMCallDetail):
                for msg in step.detail.messages:
                    messages.append(msg)
                    tool_calls = _get_field(msg, "tool_calls", [])
                    if tool_calls:
                        for tc in tool_calls:
                            tc_id = _get_field(tc, "id", "")
                            tc_name = _get_field(tc, "name", "")
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
                detected = self._detect_skill_from_tool_calls(tool_calls)
                if detected:
                    skill_read_history.append((msg_idx, detected))

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
        """Return skill name if any tool call reads a SKILL.md, else None."""
        for tool_call in tool_calls:
            name = str(_get_field(tool_call, "name")).lower()
            arguments = str(_get_field(tool_call, "arguments"))
            skill_name: Optional[str] = None

            matched = _SKILL_MD_PATTERN.search(arguments)
            if matched and self._is_skill_md_read_tool(name):
                skill_name = matched.group(1)
            elif name == "skill_tool":
                try:
                    args_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
                    if isinstance(args_dict, dict):
                        skill_name = args_dict.get("skill_name")
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.debug("[ConversationSignalDetector] failed to parse skill_tool arguments: %s", exc)

            if skill_name and self._is_existing_skill(skill_name):
                return skill_name
        return None

    def _is_existing_skill(self, skill_name: str) -> bool:
        return not self._existing_skills or skill_name in self._existing_skills

    @staticmethod
    def _is_skill_md_read_tool(name: str) -> bool:
        return not name or any(token in name for token in ("file", "read"))

    def _infer_skill_from_messages(self, messages: List[dict]) -> Optional[str]:
        skill_read_history: List[Tuple[int, str]] = []
        for msg_idx, msg in enumerate(messages):
            role = str(_get_field(msg, "role"))
            tool_calls = _get_field(msg, "tool_calls", [])
            if role == "assistant" and tool_calls:
                detected = self._detect_skill_from_tool_calls(tool_calls)
                if detected:
                    skill_read_history.append((msg_idx, detected))
        return self._resolve_active_skill(len(messages), skill_read_history)

    def _fallback_user_feedback_signals(
        self,
        user_messages: List[str],
        skill_name: str,
    ) -> List[EvolutionSignal]:
        for message in reversed(user_messages):
            if _CORRECTION_PATTERN.search(message):
                return [self._make_user_feedback_signal(message, skill_name)]
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
