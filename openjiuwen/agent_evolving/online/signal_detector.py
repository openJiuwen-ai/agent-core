# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rules-based signal extraction from conversation messages."""

from __future__ import annotations

import re
from typing import List, Optional, Set

from openjiuwen.agent_evolving.online.schema import EvolutionSignal, EvolutionCategory


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
    r"error|exception|traceback|failed|failure|timeout|timed out"
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
_TOOL_SCHEMA_PATTERN = re.compile(r"'content':\s*'---\nname:\s*[^\n]+\ndescription:", re.MULTILINE)


class SignalDetector:
    """Extract and deduplicate evolution signals from messages."""

    def __init__(self, existing_skills: Optional[Set[str]] = None) -> None:
        self._existing_skills = existing_skills or set()

    def detect(self, messages: List[dict]) -> List[EvolutionSignal]:
        """Scan messages and return deduplicated signals."""
        signals: List[EvolutionSignal] = []
        active_skill: Optional[str] = None

        for msg in messages:
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
            content = (
                msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            )
            tool_calls = (
                msg.get("tool_calls", [])
                if isinstance(msg, dict)
                else getattr(msg, "tool_calls", [])
            )

            if role == "assistant" and tool_calls:
                active_skill = self._detect_skill_from_tool_calls(tool_calls, active_skill)

            if role in ("tool", "function"):
                match = _FAILURE_KEYWORDS.search(content)
                if match:
                    if _TOOL_SCHEMA_PATTERN.search(content):
                        continue
                    tool_name = msg.get("name") or msg.get("tool_name")
                    excerpt = _extract_around_match(content, match)
                    signals.append(
                        EvolutionSignal(
                            signal_type="execution_failure",
                            evolution_type=self._classify_type(active_skill),
                            section="Troubleshooting",
                            excerpt=excerpt,
                            tool_name=tool_name,
                            skill_name=active_skill,
                        )
                    )
            elif role == "user":
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

    def _detect_skill_from_tool_calls(
        self,
        tool_calls: list,
        current_active: Optional[str],
    ) -> Optional[str]:
        """Infer active skill from read_file-like tool calls."""
        for tool_call in tool_calls:
            name = (
                tool_call.get("name")
                if isinstance(tool_call, dict)
                else getattr(tool_call, "name", "")
            )
            arguments = (
                tool_call.get("arguments")
                if isinstance(tool_call, dict)
                else getattr(tool_call, "arguments", "")
            )

            if "file" in str(name).lower() or "read" in str(name).lower():
                matched = _SKILL_MD_PATTERN.search(str(arguments))
                if matched:
                    detected_skill = matched.group(1)
                    if (
                        not self._existing_skills
                        or detected_skill in self._existing_skills
                    ):
                        return detected_skill
        return current_active

    @staticmethod
    def _deduplicate(signals: List[EvolutionSignal]) -> List[EvolutionSignal]:
        """Deduplicate by (type, excerpt[:100])."""
        seen: set[tuple] = set()
        deduped: List[EvolutionSignal] = []
        for signal in signals:
            key = (signal.signal_type, signal.excerpt[:100])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(signal)
        return deduped
