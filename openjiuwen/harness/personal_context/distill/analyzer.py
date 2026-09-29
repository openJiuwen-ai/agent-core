"""LLM analyzers — Distilly dual-track: analyzer → builder."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from openjiuwen.harness.personal_context.distill.llm import LlmPort
from openjiuwen.harness.personal_context.distill.types import CorpusMessage, DistillCandidates

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_MAX_CHARS_PER_MESSAGE = 400
_MAX_MESSAGES_IN_PROMPT = 200


def neutralize(text: str) -> str:
    """Neutralize markdown/HTML structures so corpus cannot escape the data zone."""
    return (
        (text or "")
        .replace("```", "｀｀｀")
        .replace("<!--", "〈!--")
        .replace("-->", "--〉")
    )


def load_prompt(name: str, *, subject_name: str = "本人") -> str:
    """Load a Distilly prompt file and substitute ``{name}`` placeholders."""
    path = _PROMPTS_DIR / name
    text = path.read_text(encoding="utf-8")
    return text.replace("{name}", subject_name)


def render_message_block(
    messages: list[CorpusMessage],
    *,
    conversation_titles: dict[str, str] | None = None,
) -> str:
    titles = conversation_titles or {}
    lines: list[str] = []
    for index, message in enumerate(messages[:_MAX_MESSAGES_IN_PROMPT], start=1):
        where = titles.get(message.conversation_id) or message.conversation_id or "未知会话"
        if message.is_self is True:
            who = "我"
        elif message.sender_name:
            who = neutralize(str(message.sender_name))
        elif message.sender_account:
            who = neutralize(str(message.sender_account))
        else:
            who = "他人"
        raw = neutralize((message.content_text or "")[:_MAX_CHARS_PER_MESSAGE])
        lines.append(f"#{index} [{where}] {who}: {raw}")
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value + ("\n" if value and not value.endswith("\n") else "")


def _materials_user_message(block: str, *, subject_name: str) -> str:
    return (
        f"对象姓名/代号：{subject_name}\n\n"
        "手动填写的基础信息：无（本轮仅有 IM 原材料）。\n\n"
        "原材料（按时间排序的聊天；每条带 #序号，引用证据时请用这些序号）：\n\n"
        f"{block}"
    )


class AnalyzerPort(Protocol):
    async def analyze(self, messages: list[CorpusMessage]) -> DistillCandidates:
        ...


class LlmAnalyzer:
    """Distilly pipeline: persona/work analyzer → builder → markdown candidates."""

    def __init__(
        self,
        llm: LlmPort,
        *,
        subject_name: str = "本人",
        conversation_titles: dict[str, str] | None = None,
        persona_analyzer_prompt: str | None = None,
        persona_builder_prompt: str | None = None,
        work_analyzer_prompt: str | None = None,
        work_builder_prompt: str | None = None,
    ) -> None:
        self._llm = llm
        self._subject_name = str(subject_name or "本人").strip() or "本人"
        self._conversation_titles = conversation_titles or {}
        self._persona_analyzer_prompt = persona_analyzer_prompt or load_prompt(
            "persona_analyzer.md", subject_name=self._subject_name
        )
        self._persona_builder_prompt = persona_builder_prompt or load_prompt(
            "persona_builder.md", subject_name=self._subject_name
        )
        self._work_analyzer_prompt = work_analyzer_prompt or load_prompt(
            "work_analyzer.md", subject_name=self._subject_name
        )
        self._work_builder_prompt = work_builder_prompt or load_prompt(
            "work_builder.md", subject_name=self._subject_name
        )

    async def _track(
        self,
        *,
        analyzer_system: str,
        builder_system: str,
        materials_user: str,
        track_label: str,
    ) -> str:
        analysis = _strip_fences(
            await self._llm.complete(system=analyzer_system, user=materials_user)
        )
        builder_user = (
            f"对象：{self._subject_name}\n"
            f"轨道：{track_label}\n\n"
            f"以下是 analyzer 的分析结果，请按 builder 模板生成最终 Markdown 文件内容：\n\n"
            f"{analysis}"
        )
        return _strip_fences(await self._llm.complete(system=builder_system, user=builder_user))

    async def analyze(self, messages: list[CorpusMessage]) -> DistillCandidates:
        if not messages:
            return DistillCandidates(persona_md="", work_md="")

        block = render_message_block(messages, conversation_titles=self._conversation_titles)
        materials = _materials_user_message(block, subject_name=self._subject_name)
        persona_md = await self._track(
            analyzer_system=self._persona_analyzer_prompt,
            builder_system=self._persona_builder_prompt,
            materials_user=materials,
            track_label="persona",
        )
        work_md = await self._track(
            analyzer_system=self._work_analyzer_prompt,
            builder_system=self._work_builder_prompt,
            materials_user=materials,
            track_label="work",
        )
        return DistillCandidates(persona_md=persona_md, work_md=work_md)
