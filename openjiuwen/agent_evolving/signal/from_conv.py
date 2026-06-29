# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ConversationSignalDetector converts Trajectory or messages to evolution signals."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from openjiuwen.core.common.logging import logger
from openjiuwen.agent_evolving.signal.base import (
    EvolutionCategory,
    EvolutionSignal,
    make_signal_fingerprint,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    Trajectory,
    ToolCallDetail,
)


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

# Tools whose output is fetched content (web pages, files, search results).
_DATA_FETCH_TOOLS = frozenset({
    "mcp_fetch_webpage", "fetch_webpage", "web_fetch",
    "search", "web_search", "google_search", "bing_search",
    "view_file", "read_file", "cat_file",
    "list_directory", "ls",
    "get_url", "curl", "wget",
})

# Tools that execute inline code or shell commands.
_CODE_EXEC_TOOLS = frozenset({
    "code", "bash",
    "execute_python_code", "run_python", "exec_code",
    "execute_code", "python_exec", "run_code",
})

# Parameter keys where executable content (code or commands) can be found.
_EXEC_CONTENT_KEYS = (
    "code", "code_block", "script", "source", "python_code",
    "command", "cmd", "shell_command",
)


_USER_CORRECTION_CONTENT_MAX_CHARS = 400
_CONTEXT_SNIPPET_MAX_CHARS = 1200

USER_CORRECTION_LLM_PROMPT_CN = """\
你是用户意图分析专家。判断对话中哪些用户消息包含对 Agent 行为的纠正信号。

## 用户消息列表（带索引）
{user_messages_json}

## 对话上下文摘要
{context_snippet}

## 纠正的定义

符合以下任一条件即为纠正（is_correction=true）：
- 显式否定：指出 Agent 的理解、做法、结果有错误（"不对"、"错了"、"这不是我要的"、"你搞错了"）
- 隐式纠正：含蓄表达不满并要求改变方向（"换个方式"、"这样不太合适"、"能不能试试别的"、"这个结果不理想"）
- 补充说明：用户发现 Agent 理解偏差后补充真实意图（"我的意思是…"、"其实我想要…"、"我说的不是这个"）
- 重复纠正：对同一问题再次纠正（Agent 未正确执行上次纠正）
- 否定后重新指引：用户否定 Agent 的做法并给出新方向（"不要用这个，改用…"）

不符合纠正的情况（is_correction=false）：
- 普通追问（"然后呢"、"继续"、"下一步"）
- 新需求（与 Agent 之前的做法无关的全新任务）
- 确认/赞同（"好的"、"对"、"可以"、"没问题"）
- 单纯提问（"这个怎么用"、"为什么报错"）

## 输出格式
只输出以下 JSON 数组，不要其他内容（即使没有纠正也必须输出空数组 []）：
[
  {{
    "msg_index": 0,
    "is_correction": true,
    "reason": "一句话说明为何是纠正",
    "excerpt": "相关原文片段（≤200字）"
  }}
]

要求：
1. 每条 user 消息都要判断，不可遗漏
2. msg_index 对应上方消息列表中的 index 字段
3. 仅 is_correction=true 的条目需要填写 reason 和 excerpt
4. is_correction=false 的条目只需输出 {{"msg_index": N, "is_correction": false}}
"""

USER_CORRECTION_LLM_PROMPT_EN = """\
You are a user-intent analysis expert. Determine which user messages in the conversation contain correction signals directed at the Agent's behavior.

## User Messages (with indices)
{user_messages_json}

## Conversation Context Summary
{context_snippet}

## Definition of Correction

A message is a correction (is_correction=true) if it matches any of the following:
- Explicit negation: Points out errors in the Agent's understanding, approach, or output ("that's wrong", "incorrect", "not what I asked for")
- Implicit correction: Subtly expresses dissatisfaction and requests a change ("try another approach", "this isn't quite right", "could we do it differently")
- Clarification: User clarifies true intent after noticing Agent misinterpretation ("what I meant was…", "actually I want…")
- Repeated correction: Corrects the same issue again (Agent failed to apply the previous correction)
- Redirect after negation: User rejects Agent's approach and provides a new direction ("don't use that, use… instead")

Messages that are NOT corrections (is_correction=false):
- Follow-up questions ("what's next", "continue", "next step")
- New requests (entirely new tasks unrelated to Agent's prior actions)
- Confirmations/approvals ("ok", "yes", "looks good", "that's fine")
- Plain questions ("how do I use this", "why is there an error")

## Output Format
Output only the following JSON array, nothing else (output an empty array [] even when there are no corrections):
[
  {{
    "msg_index": 0,
    "is_correction": true,
    "reason": "One sentence explaining why this is a correction",
    "excerpt": "Relevant text snippet (≤200 chars)"
  }}
]

Rules:
1. Judge every user message; do not skip any.
2. msg_index corresponds to the index field in the message list above.
3. Only is_correction=true entries need reason and excerpt.
4. For is_correction=false entries, output only {{"msg_index": N, "is_correction": false}}.
"""

USER_CORRECTION_LLM_PROMPT: Dict[str, str] = {
    "cn": USER_CORRECTION_LLM_PROMPT_CN,
    "en": USER_CORRECTION_LLM_PROMPT_EN,
}


class ConversationSignalDetector:
    """Extract evolution signals from Trajectory or message list.

    Migrated from online.SignalDetector, now accepts both Trajectory and List[dict].
    Unified interface for online and offline evolution paths.
    """

    def __init__(
        self,
        existing_skills: Optional[Set[str]] = None,
        llm: Any = None,
        model: Optional[str] = None,
        language: str = "cn",
    ) -> None:
        """Initialize detector with optional existing skills set and LLM for correction detection.

        Args:
            existing_skills: Set of skill names for skill_name resolution.
            llm: LLM client instance with an async ``invoke(model, messages)`` method.
                When provided together with *model*, ``detect_async()`` uses LLM to
                judge user corrections instead of (or in addition to) regex patterns.
            model: Model identifier string passed to ``llm.invoke()``.
            language: Prompt language, ``"cn"`` or ``"en"``.
        """
        self._existing_skills = existing_skills or set()
        self._llm = llm
        self._model = model
        self._language = language

    def detect(
        self, trajectory_or_messages: Union[Trajectory, List[dict]]
    ) -> List[EvolutionSignal]:
        """Detect evolution signals from Trajectory or messages.

        Main entry: accepts Trajectory or List[dict], returns deduplicated EvolutionSignal list.

        Args:
            trajectory_or_messages: Execution trajectory or message list.

        Returns:
            List of deduplicated EvolutionSignal.
        """
        if isinstance(trajectory_or_messages, Trajectory):
            messages = self._convert_trajectory_to_messages(trajectory_or_messages)
        else:
            messages = trajectory_or_messages
        return self._detect_from_messages(messages)

    async def detect_async(
        self, trajectory_or_messages: Union[Trajectory, List[dict]]
    ) -> List[EvolutionSignal]:
        """Async entry: uses LLM for user correction detection when available.

        When ``llm`` and ``model`` were provided at construction time, all user
        messages are sent to the LLM for correction judgment inside
        ``_detect_from_messages``.  On LLM failure the method falls back to
        the synchronous regex path automatically.

        Args:
            trajectory_or_messages: Execution trajectory or message list.

        Returns:
            Deduplicated list of EvolutionSignal.
        """
        if isinstance(trajectory_or_messages, Trajectory):
            messages = self._convert_trajectory_to_messages(trajectory_or_messages)
        else:
            messages = trajectory_or_messages

        if self._llm is not None and self._model is not None:
            try:
                return await self._detect_from_messages_async(messages)
            except Exception as exc:
                logger.warning(
                    "[FromConvSignalDetector] async detection failed (%s), "
                    "falling back to regex",
                    exc,
                )
        return self._detect_from_messages(messages)

    @staticmethod
    def _convert_trajectory_to_messages(trajectory: Trajectory) -> List[dict]:
        """Convert Trajectory.steps to message list format.

        The message format matches what SignalDetector.detect() expects:
        - LLM steps: messages from LLMCallDetail (prefers new compressed
          fields, falls back to legacy ``messages`` for old data)
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
                    tool_calls = msg.get("tool_calls", [])
                    if tool_calls:
                        for tc in tool_calls:
                            tc_id = tc.get("id", "")
                            tc_name = tc.get("name", "")
                            if tc_id and tc_name:
                                tool_call_id_to_name[tc_id] = tc_name

            elif step.kind == "tool" and isinstance(step.detail, ToolCallDetail):
                tool_name = step.detail.tool_name
                tool_call_id = (
                    step.detail.tool_call_id
                    or step.meta.get("tool_call_id", "")
                )

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
                tool_name = msg.get("name") or msg.get("tool_name") or ""
                tool_call_id = msg.get("tool_call_id", "")
                if not tool_name and tool_call_id:
                    tool_name = tool_call_id_to_name.get(tool_call_id, "")

                active_skill = self._resolve_active_skill(msg_idx, skill_read_history)

                if tool_call_id and tool_call_id in pending_scripts:
                    has_failure = bool(_FAILURE_KEYWORDS.search(content)) if content else False
                    if not has_failure:
                        logger.info(
                            "[FromConvSignalDetector] tool_call_id: %s, pending_scripts: %s",
                            tool_call_id,
                            pending_scripts[tool_call_id],
                        )
                        signals.append(
                            EvolutionSignal(
                                signal_type="script_artifact",
                                evolution_type=self._classify_type(active_skill),
                                section="Scripts",
                                excerpt=pending_scripts[tool_call_id][:600],
                                tool_name=tool_name,
                                skill_name=active_skill,
                            )
                        )
                    del pending_scripts[tool_call_id]

                if tool_name.lower() in _DATA_FETCH_TOOLS:
                    continue

                match = _FAILURE_KEYWORDS.search(content)
                logger.info("[FromConvSignalDetector] _FAILURE_KEYWORDS match: %s", match)
                if match:
                    if _TOOL_SCHEMA_PATTERN.search(content):
                        continue
                    excerpt = _extract_around_match(content, match)
                    logger.info("[FromConvSignalDetector] _extract_around_match excerpt: %s", excerpt)
                    signals.append(
                        EvolutionSignal(
                            signal_type="execution_failure",
                            evolution_type=self._classify_type(active_skill),
                            section="Troubleshooting",
                            excerpt=excerpt,
                            tool_name=tool_name or None,
                            skill_name=active_skill,
                        )
                    )
            elif role == "user":
                active_skill = self._resolve_active_skill(msg_idx, skill_read_history)
                match = _CORRECTION_PATTERN.search(content)
                logger.info("[FromConvSignalDetector] _CORRECTION_PATTERN match: %s", match)
                if match:
                    excerpt = _extract_around_match(content, match)
                    logger.info("[FromConvSignalDetector] _extract_around_match excerpt: %s", excerpt)
                    signals.append(
                        EvolutionSignal(
                            signal_type="user_correction",
                            evolution_type=self._classify_type(active_skill),
                            section="Examples",
                            excerpt=excerpt,
                            skill_name=active_skill,
                        )
                    )

        return self._deduplicate(signals)

    async def _detect_from_messages_async(self, messages: List[dict]) -> List[EvolutionSignal]:
        """Async variant: same structure as _detect_from_messages but uses LLM
        for user correction detection.  All user messages are batched into a
        single LLM call to avoid redundant API calls."""

        # --- Pre-pass: batch LLM judgment for all user messages ---------------
        # None = LLM failed (fallback to regex); dict = LLM succeeded
        llm_corrections: Optional[Dict[int, dict]] = None
        try:
            llm_corrections = await self._batch_judge_corrections(messages)
        except Exception as exc:
            logger.warning(
                "[FromConvSignalDetector] batch LLM correction judgment failed (%s), "
                "will fall back to regex",
                exc,
            )

        # --- Main detection loop (mirrors _detect_from_messages) ---------------
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
                tool_name = msg.get("name") or msg.get("tool_name") or ""
                tool_call_id = msg.get("tool_call_id", "")
                if not tool_name and tool_call_id:
                    tool_name = tool_call_id_to_name.get(tool_call_id, "")

                active_skill = self._resolve_active_skill(msg_idx, skill_read_history)

                if tool_call_id and tool_call_id in pending_scripts:
                    has_failure = bool(_FAILURE_KEYWORDS.search(content)) if content else False
                    if not has_failure:
                        logger.info(
                            "[FromConvSignalDetector] tool_call_id: %s, pending_scripts: %s",
                            tool_call_id,
                            pending_scripts[tool_call_id],
                        )
                        signals.append(
                            EvolutionSignal(
                                signal_type="script_artifact",
                                evolution_type=self._classify_type(active_skill),
                                section="Scripts",
                                excerpt=pending_scripts[tool_call_id][:600],
                                tool_name=tool_name,
                                skill_name=active_skill,
                            )
                        )
                    del pending_scripts[tool_call_id]

                if tool_name.lower() in _DATA_FETCH_TOOLS:
                    continue

                match = _FAILURE_KEYWORDS.search(content)
                logger.info("[FromConvSignalDetector] _FAILURE_KEYWORDS match: %s", match)
                if match:
                    if _TOOL_SCHEMA_PATTERN.search(content):
                        continue
                    excerpt = _extract_around_match(content, match)
                    logger.info("[FromConvSignalDetector] _extract_around_match excerpt: %s", excerpt)
                    signals.append(
                        EvolutionSignal(
                            signal_type="execution_failure",
                            evolution_type=self._classify_type(active_skill),
                            section="Troubleshooting",
                            excerpt=excerpt,
                            tool_name=tool_name or None,
                            skill_name=active_skill,
                        )
                    )
            elif role == "user":
                active_skill = self._resolve_active_skill(msg_idx, skill_read_history)
                if llm_corrections is not None:
                    # LLM 调用成功，查结果判断是否为纠正
                    if msg_idx in llm_corrections:
                        correction = llm_corrections[msg_idx]
                        excerpt = correction.get("excerpt", content[:400])
                        logger.info(
                            "[FromConvSignalDetector] LLM correction at msg_idx=%d: %s",
                            msg_idx, correction.get("reason", ""),
                        )
                        signals.append(
                            EvolutionSignal(
                                signal_type="user_correction",
                                evolution_type=self._classify_type(active_skill),
                                section="Examples",
                                excerpt=excerpt,
                                skill_name=active_skill,
                            )
                        )
                    # msg_idx 不在 llm_corrections 中 → LLM 判断不是纠正，跳过
                else:
                    # LLM 调用失败（None），fallback 到正则
                    match = _CORRECTION_PATTERN.search(content)
                    logger.info("[FromConvSignalDetector] _CORRECTION_PATTERN match: %s", match)
                    if match:
                        excerpt = _extract_around_match(content, match)
                        logger.info("[FromConvSignalDetector] _extract_around_match excerpt: %s", excerpt)
                        signals.append(
                            EvolutionSignal(
                                signal_type="user_correction",
                                evolution_type=self._classify_type(active_skill),
                                section="Examples",
                                excerpt=excerpt,
                                skill_name=active_skill,
                            )
                        )

        return self._deduplicate(signals)

    async def _batch_judge_corrections(self, messages: List[dict]) -> Optional[Dict[int, dict]]:
        """Batch-send all user messages to LLM in a single call.

        Returns:
            Dict mapping msg_idx -> {msg_index, is_correction, reason, excerpt}
            for messages judged as corrections.  Empty dict when LLM succeeded
            but found no corrections.  None when LLM call or parse failed.
        """
        skill_read_history: List[Tuple[int, str]] = []
        user_entries: List[dict] = []

        for msg_idx, msg in enumerate(messages):
            role = str(_get_field(msg, "role"))
            if role == "assistant" and _get_field(msg, "tool_calls", []):
                detected = self._detect_skill_from_tool_calls(_get_field(msg, "tool_calls", []))
                if detected:
                    skill_read_history.append((msg_idx, detected))
            elif role == "user":
                content = str(_get_field(msg, "content"))
                if not content.strip():
                    continue
                truncated = content[:_USER_CORRECTION_CONTENT_MAX_CHARS]
                if len(content) > _USER_CORRECTION_CONTENT_MAX_CHARS:
                    truncated += "..."
                user_entries.append({
                    "index": len(user_entries),
                    "msg_idx": msg_idx,
                    "content": truncated,
                })

        if not user_entries:
            return {}

        # 只传递最近一次 user 消息
        last_user = user_entries[-1]
        context_snippet = self._build_correction_context(messages)
        user_messages_json = json.dumps(
            [{"index": last_user["index"], "content": last_user["content"]}],
            ensure_ascii=False, indent=2,
        )
        lang = self._language if self._language in USER_CORRECTION_LLM_PROMPT else "cn"
        prompt = USER_CORRECTION_LLM_PROMPT[lang].format(
            user_messages_json=user_messages_json,
            context_snippet=context_snippet or "(无上下文 / no context)",
        )

        raw = await self._invoke_correction_llm(prompt)
        parsed = self._parse_correction_response(raw)
        if parsed is None:
            # Retry once
            logger.warning("[FromConvSignalDetector] batch correction LLM parse failed, retrying")
            raw = await self._invoke_correction_llm(prompt)
            parsed = self._parse_correction_response(raw)
        if parsed is None:
            return None

        index_to_msg_idx = {last_user["index"]: last_user["msg_idx"]}
        result: Dict[int, dict] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            msg_idx = index_to_msg_idx.get(item.get("msg_index"))
            if msg_idx is not None and item.get("is_correction"):
                result[msg_idx] = {
                    "msg_index": item.get("msg_index"),
                    "is_correction": item.get("is_correction"),
                    "reason": str(item.get("reason", "")),
                    "excerpt": str(item.get("excerpt", ""))[:400],
                }

        logger.info(
            "[FromConvSignalDetector] batch LLM judgment: %d user msgs -> %d corrections",
            len(user_entries), len(result),
        )
        return result

    async def _invoke_correction_llm(self, prompt: str) -> Optional[str]:
        """Call LLM and return raw text; returns None on exception."""
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[FromConvSignalDetector] correction LLM call failed: %s", exc)
            return None

    @staticmethod
    def _parse_correction_response(raw: Optional[str]) -> Optional[List[dict]]:
        """Parse LLM JSON array response. Returns None on failure."""
        if not raw or not raw.strip():
            return None
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE).strip()
        text = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            matched = re.search(r"\[[\s\S]*\]", text)
            if not matched:
                return None
            try:
                data = json.loads(matched.group(0))
            except json.JSONDecodeError:
                return None
        return data if isinstance(data, list) else None

    @staticmethod
    def _build_correction_context(messages: List[dict], max_chars: int = _CONTEXT_SNIPPET_MAX_CHARS) -> str:
        """Build compact context from recent assistant/tool messages for LLM grounding."""
        parts: List[str] = []
        total = 0
        for msg in reversed(messages):
            role = str(_get_field(msg, "role"))
            if role not in ("assistant", "tool", "function"):
                continue
            content = str(_get_field(msg, "content"))
            if not content.strip():
                continue
            if role == "assistant":
                tool_calls = _get_field(msg, "tool_calls", [])
                if tool_calls:
                    names = [str(_get_field(tc, "name", "")) for tc in tool_calls if isinstance(tc, dict)]
                    snippet = f"[assistant] tool_calls: {', '.join(names)}"
                else:
                    snippet = f"[assistant] {content[:200]}"
            else:
                tool_name = _get_field(msg, "name", "tool")
                snippet = f"[{tool_name}] {content[:150]}"
            if total + len(snippet) > max_chars:
                break
            parts.append(snippet)
            total += len(snippet)
        parts.reverse()
        return "\n".join(parts)

    @staticmethod
    def _classify_type(skill_name: Optional[str]) -> EvolutionCategory:
        """Classify evolution signal type."""
        _ = skill_name
        return EvolutionCategory.SKILL_EXPERIENCE

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

            # Path 1: Detect file read tools that access SKILL.md
            if "file" in name or "read" in name:
                matched = _SKILL_MD_PATTERN.search(arguments)
                if matched:
                    skill_name = matched.group(1)
            elif name == "skill_tool":
                try:
                    args_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
                    if isinstance(args_dict, dict):
                        skill_name = args_dict.get("skill_name")
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.debug("[ConversationSignalDetector] failed to parse skill_tool arguments: %s", exc)

            if skill_name and (not self._existing_skills or skill_name in self._existing_skills):
                return skill_name
        return None

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
        """Deduplicate by (type, tool_name, skill_name, excerpt[:200])."""
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