# coding: utf-8
"""ICLRReviewRail —— 评审门禁 Rail（框架贡献点 3）。

把"论文是否达到 ICLR 审稿标准"作为 TaskCompletion 的自定义停止谓词：
写作/修订阶段的 DeepAgent 不能只输出文本就收敛，必须通过结构化评审
（soundness / contribution / clarity / reproducibility 四维 1-10 打分）
并达到阈值，Rail 才允许任务完成；否则把评审意见作为 follow-up 注回，
驱动下一轮修订（对应 FARS 的"评审-修订闭环"，但以 Rail 形式内生化）。

同时暴露 ``export_review_report`` 供 pipeline 汇总每轮评分曲线。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail


@dataclass
class ReviewScore:
    """一轮结构化评审结果。"""
    round_idx: int
    soundness: float
    contribution: float
    clarity: float
    reproducibility: float
    overall: float
    decision: str                 # accept / borderline / reject
    comments: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_REVIEW_PROMPT = """You are an ICLR reviewer. Review the following paper draft strictly.

Score each dimension from 1-10 and give a short justification:
- soundness
- contribution
- clarity
- reproducibility

Then give an overall score (1-10) and a decision in {{accept, borderline, reject}}.
Finally list concrete, actionable revision comments (bullet points).

Respond ONLY with a JSON object of the form:
{{
  "soundness": <float>, "contribution": <float>, "clarity": <float>,
  "reproducibility": <float>, "overall": <float>, "decision": "<accept|borderline|reject>",
  "comments": "<bullet points as one string>"
}}

=== PAPER DRAFT ===
{draft}
=== END DRAFT ===
"""


class ICLRReviewRail(DeepAgentRail):
    """评审门禁：不达标不收敛，评审意见作为 follow-up 驱动修订。

    Args:
        judge_model:    评审用 openjiuwen Model（可用与写作不同的模型做交叉评审）。
        accept_overall: overall ≥ 该值才判 accept（默认 7.0，对齐 ICLR 接收线）。
        max_rounds:     最多评审轮数（超出后强制收敛，避免死循环）。
        report_path:    每轮评分 JSONL 落盘路径。
    """

    priority = 60

    def __init__(
        self,
        judge_model,
        *,
        accept_overall: float = 7.0,
        max_rounds: int = 3,
        report_path: str | Path = "workspace/review/review_log.jsonl",
    ) -> None:
        super().__init__()
        self._judge = judge_model
        self._accept_overall = accept_overall
        self._max_rounds = max_rounds
        self._report_path = Path(report_path)
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        self._round = 0
        self._scores: list[ReviewScore] = []

    # --------------------------------------------------------------- helpers
    async def _run_review(self, draft: str) -> ReviewScore:
        self._round += 1
        prompt = _REVIEW_PROMPT.format(draft=draft[:120_000])  # 防爆长度
        messages = [{"role": "user", "content": prompt}]
        # Native Model exposes invoke; retain ainvoke/generate adapter compatibility.
        if hasattr(self._judge, "ainvoke"):
            resp = await self._judge.ainvoke(messages)
        elif hasattr(self._judge, "invoke"):
            resp = await self._judge.invoke(messages)
        else:
            resp = await self._judge.generate(messages=messages)
        text = getattr(resp, "content", None) or (resp if isinstance(resp, str) else str(resp))
        score = self._parse_review(text)
        self._scores.append(score)
        with self._report_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(score.to_dict(), ensure_ascii=False) + "\n")
        return score

    def _parse_review(self, text: str) -> ReviewScore:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        payload: dict[str, Any] = {}
        if m:
            try:
                payload = json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                payload = {}
        def _f(name: str) -> float:
            try:
                return float(payload.get(name, 5.0))
            except Exception:  # noqa: BLE001
                return 5.0
        return ReviewScore(
            round_idx=self._round,
            soundness=_f("soundness"),
            contribution=_f("contribution"),
            clarity=_f("clarity"),
            reproducibility=_f("reproducibility"),
            overall=_f("overall"),
            decision=str(payload.get("decision", "borderline")).lower(),
            comments=str(payload.get("comments", "")),
        )

    # ----------------------------------------------------------------- hooks
    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:  # noqa: D102
        """每轮写作任务结束后：评审 → 达标则放行 / 不达标则注回意见。"""
        if self._round >= self._max_rounds:
            return
        # 从 ctx 提取最新草稿文本（具体字段按 DeepAgent 上下文结构兜底）
        draft = self._extract_latest_draft(ctx)
        if not draft or len(draft) < 2000:  # 不是完整论文草稿就跳过
            return
        try:
            score = await self._run_review(draft)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ICLRReviewRail] review failed: %s", exc)
            return
        if score.overall < self._accept_overall and self._round < self._max_rounds:
            followup = (
                "[ICLR Review Gate] The current draft scored overall="
                f"{score.overall:.1f} (< {self._accept_overall}). "
                "Revise the draft addressing these reviewer comments, then resubmit:\n"
                + score.comments
            )
            agent = getattr(ctx, "agent", None)
            controller = getattr(agent, "_loop_controller", None)
            if controller is not None:
                controller.enqueue_follow_up(followup)

    @staticmethod
    def _extract_latest_draft(ctx: AgentCallbackContext) -> str:
        """尽力从回调上下文拿到最新论文草稿文本。"""
        for attr in ("outputs", "result", "output"):
            val = getattr(ctx, attr, None)
            if isinstance(val, str) and len(val) > 2000:
                return val
            if val is not None and hasattr(val, "content"):
                content = getattr(val, "content")
                if isinstance(content, str) and len(content) > 2000:
                    return content
        return ""

    # ------------------------------------------------------------ public API
    def export_review_report(self) -> dict[str, Any]:  # noqa: D102
        return {
            "rounds": len(self._scores),
            "scores": [s.to_dict() for s in self._scores],
            "final_overall": self._scores[-1].overall if self._scores else None,
        }
