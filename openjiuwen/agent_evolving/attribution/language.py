# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Output-language directive shared by every attribution-stage prompt.

The prompt files already select a language, but a Chinese trajectory can still
make the model answer in Chinese under an English prompt (and the reverse).
The directive below tells the model explicitly which language to write the
user-facing output in, independently of the input language.
"""

from __future__ import annotations


def output_language_directive(language: str) -> str:
    """Return the explicit output-language instruction for ``language``."""
    if str(language).strip().lower() in {"zh", "cn"}:
        return (
            "输出语言：中文。无论输入消息或轨迹使用什么语言，所有面向用户的输出"
            "（任务描述、要求、反馈、评语、建议、轨迹摘要等）必须使用中文。"
        )
    return (
        "Output language: English. Regardless of the language of the input messages "
        "or trajectory, all user-facing output (task description, requirements, "
        "feedback, comments, suggestions, trajectory summary, etc.) must be written "
        "in English."
    )


__all__ = ["output_language_directive"]
