# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Load cached, stage-specific Chinese/English summary instructions."""

from functools import lru_cache
from pathlib import Path

from openjiuwen.agent_evolving.attribution.language import output_language_directive

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_TEXTS = {
    "en": {
        "guidance": {
            "segment": "Summarize this segment's intent, key operations, results and local ending state.",
            "segment_batch": "Summarize every input segment independently and return one results item per segment_id.",
            "turn": (
                "Connect this turn's input, segment actions, results and ending state; "
                "update known issues with later evidence. Avoid repeating each segment verbatim."
            ),
            "agent": (
                "Summarize responsibilities, progress across turns, handoffs and outcome; "
                "update known issues across local turns. Collapse repeated tuning, approvals and edits "
                "into their purpose and final result; retain consequential changes, verification gaps "
                "and key node references."
            ),
            "root": (
                "Return narrative for later diagnosis, display_summary for users, and updated problems, "
                "following the separate audience requirements in the system instructions."
            ),
        },
        "report": "Submit the summary once using report_summary. Include nonempty narrative and problems.",
        "root_report": (
            "Submit the summary once using report_summary. "
            "Include nonempty narrative, display_summary and problems."
        ),
        "batch_report": "Submit one report_summary call containing results; each item needs segment_id, nonempty narrative and problems.",
        "sources": (
            "Copy complete leaf source_problem_ids from supplied issues; never use node IDs or invent sources. "
            "Omit unsupported new issues."
        ),
        "dismissed": (
            "Leaf source IDs proven normal, irrelevant or trivial recovered noise. "
            "Do not dismiss material unresolved issues."
        ),
        "retry": (
            "Previous response failed: {error}. "
            "Return exactly one report_summary with nonempty narrative, problems and valid evidence."
        ),
        "root_retry": (
            "Previous response failed: {error}. Return exactly one report_summary "
            "with nonempty narrative, display_summary, problems and valid evidence."
        ),
        "batch_retry": "Previous response failed: {error}. Return one report_summary with results for every input segment_id.",
        "shortened": (
            " Some long fields were shortened after a provider context rejection; "
            "disclose missing detail and do not invent facts."
        ),
    },
    "zh": {
        "guidance": {
            "segment": "概括本块意图、关键操作、实质结果和局部结束状态。",
            "segment_batch": "分别总结每个输入 Segment，在 results 中为每个 segment_id 返回一项独立结果。",
            "turn": "串联本轮输入、各块操作、结果与结束状态，用后续证据更新已有问题；不要逐块照抄。",
            "agent": "总结主体职责、跨轮进展、交接和最终结果，更新主体范围内的问题状态；重复调参、审批和编辑合并为目的与最终结果，保留实质变化、验证缺口和关键节点定位。",
            "root": "按系统中两种读者的要求，返回供诊断使用的 narrative、面向用户的 display_summary 和更新后的 problems。",
        },
        "report": "使用 report_summary 一次提交摘要，包含非空 narrative 和 problems。",
        "root_report": "使用 report_summary 一次提交摘要，包含非空 narrative、display_summary 和 problems。",
        "batch_report": "只调用一次 report_summary 返回 results；每项包含 segment_id、非空 narrative 和 problems。",
        "sources": "照抄输入问题的完整叶子 source_problem_ids；不能使用节点 ID 或编造来源，不支持的新增问题不要输出。",
        "dismissed": "已证实为正常、无关或轻微已恢复噪声的叶子问题来源 ID；不得排除实质未解决问题。",
        "retry": "上次返回未通过校验：{error}。请只调用一次 report_summary，包含非空 narrative、problems 和合法证据。",
        "root_retry": "上次返回未通过校验：{error}。请只调用一次 report_summary，包含非空 narrative、display_summary、problems 和合法证据。",
        "batch_retry": "上次返回未通过校验：{error}。请只调用一次 report_summary，在 results 中返回每个输入 segment_id 的结果。",
        "shortened": " 服务拒绝超长上下文后，部分长字段已缩短；请说明缺失细节，不要编造事实。",
    },
}


@lru_cache(maxsize=10)
def _load_prompt(language: str, name: str) -> str:
    path = _PROMPT_DIR / language / f"{name}.md"
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Empty trajectory summary prompt: {path}")
    return content


def summary_prompts(language: str, stage: str = "segment") -> dict:
    """Match system sections and protocol text to the actual request stage."""
    if stage not in {"segment", "segment_batch", "turn", "agent", "root"}:
        raise ValueError(f"Unknown trajectory summary stage: {stage}")
    value = str(language).strip().lower().replace("_", "-")
    key = "zh" if value in {"zh", "cn"} or value.startswith("zh-") else "en"
    names = ["common", "segment" if stage in {"segment", "segment_batch"} else "merge"]
    if stage == "segment_batch":
        names.append("segment_batch")
    if stage == "root":
        names.append("root")
    system = "\n\n".join([*(_load_prompt(key, name) for name in names), output_language_directive(key)])
    texts = _TEXTS[key]
    prefix = "root_" if stage == "root" else "batch_" if stage == "segment_batch" else ""
    return {**texts, "language": key, "system": system,
            "report": texts[prefix + "report"], "retry": texts[prefix + "retry"]}
