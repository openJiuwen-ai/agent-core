"""Nudges the coding agent to stop exploring and attempt an implementation
once it has gone too many tool calls without writing anything to output/.

Observed directly across multiple runs: when the coding agent hits an
environment surprise (a broken interpreter alias, an unfamiliar SDK
signature, a silent subprocess crash), it tends to escalate reconnaissance
-- more bash probes, more source-code reading -- rather than trying an
implementation with what it already knows. Every retry attempt then fails
identically at the same "missing entry point run.py" check, because the
agent never got there. TaskCompletionRail already bounds the *total*
iteration count; this bounds how much of that budget can be spent on
exploration with zero deliverable progress before a reminder fires.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

_SECTION_NAME = "exploration_budget_warning"
_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})

_WARNING_CN = (
    "## 探索预算提醒\n\n"
    "你已经连续 {count} 次工具调用没有往 output/ 写入任何交付物文件了。"
    "不要再继续排查环境或阅读 SDK 源码——先用你现在已经知道的信息，对 "
    "output/ 下的入口文件做一次实现尝试，哪怕不完美。可以在后续轮次里修正，"
    "但必须先有一个可以被校验的版本。"
)
_WARNING_EN = (
    "## Exploration budget warning\n\n"
    "You have made {count} consecutive tool calls without writing any "
    "deliverable file under output/. Stop investigating the environment or "
    "reading SDK source further -- attempt an implementation of the entry "
    "point under output/ with what you already know, even if imperfect. "
    "You can refine it in a later turn, but there must be a version on disk "
    "that can be validated first."
)
_WARNING_TEXT = {"cn": _WARNING_CN, "en": _WARNING_EN}


def _is_output_write(tool_name: str, tool_args: Any) -> bool:
    if tool_name not in _WRITE_TOOL_NAMES:
        return False
    file_path = ""
    if isinstance(tool_args, dict):
        file_path = str(tool_args.get("file_path") or "")
    else:
        file_path = str(getattr(tool_args, "file_path", "") or "")
    normalized = file_path.replace("\\", "/").strip("/").split("/")
    return "output" in normalized


class ExplorationBudgetRail(DeepAgentRail):
    """Reminds the agent to attempt an implementation after too much
    exploration with no deliverable progress. See module docstring."""

    priority = 90

    def __init__(self, *, threshold: int = 30) -> None:
        super().__init__()
        self._threshold = threshold
        self._calls_since_write = 0
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(_SECTION_NAME)

    async def after_tool_call(self, ctx) -> None:
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "")
        tool_args = getattr(inputs, "tool_args", None)
        if _is_output_write(tool_name, tool_args):
            self._calls_since_write = 0
        else:
            self._calls_since_write += 1

    async def before_model_call(self, ctx) -> None:
        if self.system_prompt_builder is None:
            return
        if self._calls_since_write >= self._threshold:
            language = getattr(self.system_prompt_builder, "language", "cn") or "cn"
            text = _WARNING_TEXT.get(language, _WARNING_TEXT["cn"]).format(count=self._calls_since_write)
            self.system_prompt_builder.add_section(
                PromptSection(name=_SECTION_NAME, content={language: text}, priority=90)
            )
        else:
            self.system_prompt_builder.remove_section(_SECTION_NAME)


__all__ = [
    "ExplorationBudgetRail",
]
