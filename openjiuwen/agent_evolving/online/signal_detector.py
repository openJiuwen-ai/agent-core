# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rules-based signal extraction from conversation messages."""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Set, Tuple

from openjiuwen.agent_evolving.online.schema import EvolutionSignal, EvolutionCategory


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


def make_signal_fingerprint(signal: EvolutionSignal) -> Tuple[str, str, str, str]:
    """Build a dedup fingerprint for an evolution signal.

    Shared by SignalDetector and SkillEvolutionRail to keep fingerprints consistent.
    """
    return (
        signal.signal_type,
        signal.tool_name or "",
        signal.skill_name or "",
        signal.excerpt[:200],
    )


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
_TOOL_SCHEMA_PATTERN = re.compile(r"\{'content': '---\\nname: [^\\n]+\\ndescription:")

# Tools whose output is fetched content (web pages, files, search results).
# Failure keywords in their output are from the content itself, not execution errors.
_DATA_FETCH_TOOLS = frozenset({
    "mcp_fetch_webpage", "fetch_webpage", "web_fetch",
    "search", "web_search", "google_search", "bing_search",
    "view_file", "read_file", "cat_file",
    "list_directory", "ls",
    "get_url", "curl", "wget",
})

# Tools that execute inline code or shell commands.
_CODE_EXEC_TOOLS = frozenset({
    # jiuwenclaw native
    "code", "bash",
    # common aliases across frameworks
    "execute_python_code", "run_python", "exec_code",
    "execute_code", "python_exec", "run_code",
})

# Parameter keys where executable content (code or commands) can be found.
_EXEC_CONTENT_KEYS = (
    "code", "code_block", "script", "source", "python_code",
    "command", "cmd", "shell_command",
)


class SignalDetector:
    """Extract and deduplicate evolution signals from messages."""

    def __init__(self, existing_skills: Optional[Set[str]] = None) -> None:
        self._existing_skills = existing_skills or set()

    def detect(self, messages: List[dict]) -> List[EvolutionSignal]:
        """Scan messages and return deduplicated signals."""
        signals: List[EvolutionSignal] = []
        skill_read_history: List[Tuple[int, str]] = []
        pending_scripts: Dict[str, str] = {}
        # tool-result messages may lack 'name'; this mapping recovers it
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
                if match:
                    if _TOOL_SCHEMA_PATTERN.search(content):
                        continue
                    excerpt = _extract_around_match(content, match)
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
                if match:
                    excerpt = _extract_around_match(content, match)
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

    @staticmethod
    def _classify_type(skill_name: Optional[str]) -> EvolutionCategory:
        """Classify evolution signal type.

        NOTE: current version maps all signals to skill experience.
        """
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
            if "file" not in name and "read" not in name:
                continue
            arguments = str(_get_field(tool_call, "arguments"))
            matched = _SKILL_MD_PATTERN.search(arguments)
            if matched:
                skill = matched.group(1)
                if not self._existing_skills or skill in self._existing_skills:
                    return skill
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
