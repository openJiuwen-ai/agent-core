# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Literal

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig, UserMessage

_L1_SUMMARY_PROMPT = (
    "将以下本轮完整语料压缩为不超过 {max_chars} 字的目录摘要。"
    "必须覆盖：用户诉求、每一个 [tool_call] 的工具名与核心参数、"
    "每一个 [tool_result] 的关键结论（尤其是并行子任务的各自产出），"
    "以及最终结论要点。不得只复述末尾 A: 而遗漏中间工具产出。"
    "只输出摘要正文，格式：\nQ: ...\n[工具与产出要点]: ...\nA: ...\n\n"
    "待压缩内容：\n{text}"
)


class QABlockSummarizer:
    """Generate L1 catalog text from user query + final answer."""

    def __init__(self, config: QABlockConfig | None = None):
        self._config = config or QABlockConfig()
        self._model: Model | None = None

    def bind_model_defaults(
        self,
        model_config: ModelRequestConfig | None,
        model_client_config: ModelClientConfig | None,
    ) -> None:
        if model_config is not None and model_client_config is not None:
            self._model = Model(model_client_config, model_config)

    async def generate_l1(
        self,
        user_query: str,
        final_answer: str,
        *,
        model: Any | None = None,
        allow_llm: bool = True,
        corpus: str | None = None,
    ) -> tuple[str, Literal["inline", "compressed"]]:
        """Generate L1 catalog line from Q+A.

        Tiering (see freeze_commit vs freeze_persist):
        - inline: len(Q+A) <= l1_inline_max_chars
        - truncate: longer but allow_llm=False, or below l1_llm_min_chars, or LLM unavailable
        - llm: allow_llm=True and len(Q+A) >= l1_llm_min_chars
        """
        user_query = (user_query or "").strip()
        final_answer = (final_answer or "").strip()
        inline_text = f"Q: {user_query}\nA: {final_answer}".strip()
        source_text = corpus or inline_text
        combined_len = len(source_text)

        if combined_len <= self._config.l1_inline_max_chars and corpus is None:
            logger.info(
                "[QABlockSummarizer] inline L1 chars=%s allow_llm=%s",
                len(inline_text),
                allow_llm,
            )
            return inline_text, "inline"

        if not allow_llm or combined_len < self._config.l1_llm_min_chars:
            summary = self._truncate_summary(source_text)
            logger.info(
                "[QABlockSummarizer] truncated L1 source_chars=%s summary_chars=%s allow_llm=%s",
                len(source_text),
                len(summary),
                allow_llm,
            )
            return summary, "compressed"

        llm_model = model or self._model
        if llm_model is None:
            logger.warning(
                "[QABlockSummarizer] llm unavailable, fallback truncated source_chars=%s",
                len(source_text),
            )
        else:
            summary = await self._summarize_with_llm(source_text, llm_model)
            if summary:
                logger.info(
                    "[QABlockSummarizer] llm L1 source_chars=%s summary_chars=%s",
                    len(source_text),
                    len(summary),
                )
                return summary, "compressed"
            logger.warning(
                "[QABlockSummarizer] llm empty response, fallback truncated source_chars=%s",
                len(source_text),
            )

        summary = self._truncate_summary(source_text)
        logger.info(
            "[QABlockSummarizer] fallback truncated L1 source_chars=%s summary_chars=%s",
            len(source_text),
            len(summary),
        )
        return summary, "compressed"

    def _truncate_summary(self, inline_text: str) -> str:
        max_chars = self._config.l1_summary_max_chars
        summary = inline_text[:max_chars]
        if len(inline_text) > max_chars:
            summary = summary.rstrip() + "…"
        return summary

    async def _summarize_with_llm(self, inline_text: str, model: Any) -> str:
        try:
            cap = max(8000, self._config.l1_summary_max_chars * 4)
            prompt = _L1_SUMMARY_PROMPT.format(
                max_chars=self._config.l1_summary_max_chars,
                text=inline_text[:cap],
            )
            invoke = getattr(model, "invoke", None)
            if not callable(invoke):
                return ""
            response = await invoke([UserMessage(content=prompt)], tools=None)
            content = (getattr(response, "content", None) or str(response) or "").strip()
            if not content:
                logger.warning("[QABlockSummarizer] llm invoke returned empty content")
                return ""
            if len(content) > self._config.l1_summary_max_chars:
                content = content[: self._config.l1_summary_max_chars].rstrip() + "…"
            return content
        except Exception as exc:
            logger.warning("[QABlockSummarizer] llm summarize failed: %s", exc)
            return ""
