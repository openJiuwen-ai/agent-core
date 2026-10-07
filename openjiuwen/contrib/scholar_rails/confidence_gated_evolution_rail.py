# coding: utf-8
"""ConfidenceGatedSkillEvolutionRail —— 置信门控的技能自演进（框架贡献点 4，论文方法核心）。

问题背景
--------
openJiuwen 原生 ``SkillEvolutionRail`` 在检测到执行信号（execution_failure /
script_artifact）后即触发 LLM 生成演进记录。这在长任务序列上存在两个缺陷：
  1. **噪声演进**：偶发失败（网络抖动、任务本身歧义）会被误当作"可复用经验"，
     导致 SKILL.md 被低质量记录污染；
  2. **资源浪费**：无差别演进会持续消耗 LLM 调用，抬升 token 成本。

本 Rail 在原生信号之上叠加**双门控**（论文核心机制）：
  * **成功率滑窗门控**：统计最近 ``window`` 个任务的成功率，仅当成功率出现
    "可改进空间"（低于上界 1.0）且样本量足够时才允许演进，避免在已收敛阶段空转；
  * **置信度门控**：演进记录入库前由 LLM 自评"该经验对未来任务的可复用性"
    (0-1)，仅当 ≥ ``min_confidence`` 才持久化，否则丢弃并计入负样本。

两条门控共同把"演进次数"从 O(信号数) 压到 O(真正可复用经验数)，
是论文实验里 EvolveSkill 条件组的核心实现。

该 Rail 通过组合（持有一个原生 ``SkillEvolutionRail`` 的引用并在其
信号回调前/后插入门控逻辑）工作，不 fork 原生实现，保证兼容性。
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail


@dataclass
class TaskOutcome:
    """单任务结果（供成功率滑窗统计）。"""

    task_id: str
    success: bool
    ts: float = field(default_factory=time.time)


@dataclass
class EvolutionGateLog:
    """一次门控判定的审计记录。"""

    ts: float
    allowed: bool
    reason: str
    recent_success_rate: float
    confidence: float = 0.0


class ConfidenceGatedSkillEvolutionRail(DeepAgentRail):
    """在原生 SkillEvolutionRail 之外叠加成功率滑窗 + 置信度双门控。

    Args:
        window:            成功率滑窗大小（最近 N 个任务）。
        min_samples:       触发门控所需的最小样本量。
        min_confidence:    演进记录入库的最低置信度阈值（0-1）。
        gate_log_path:     门控审计日志（JSONL）。
        judge_model:       置信度评估用模型；为 None 时退化为仅成功率门控。
    """

    priority = 85  # 高于原生 SkillEvolutionRail(80)，先拦截

    def __init__(
        self,
        *,
        window: int = 10,
        min_samples: int = 4,
        min_confidence: float = 0.6,
        gate_log_path: str | Path = "workspace/evolution/gate_log.jsonl",
        judge_model: Any = None,
    ) -> None:
        super().__init__()
        if window < 1:
            raise ValueError("window must be >= 1")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")
        self._window = window
        self._min_samples = min_samples
        self._min_confidence = min_confidence
        self._gate_log_path = Path(gate_log_path)
        self._gate_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._judge = judge_model
        self._outcomes: Deque[TaskOutcome] = deque(maxlen=window)
        self._gate_logs: list[EvolutionGateLog] = []

    # ------------------------------------------------------------ public API
    def record_task_outcome(self, task_id: str, success: bool) -> None:
        """由基准运行器在每次任务判定后调用，喂入成功率滑窗。"""
        self._outcomes.append(TaskOutcome(task_id=task_id, success=success))

    @property
    def recent_success_rate(self) -> float:
        """最近 window 个任务的成功率；样本不足返回 -1（表示数据不足）。"""
        if len(self._outcomes) < self._min_samples:
            return -1.0
        return sum(1 for o in self._outcomes if o.success) / len(self._outcomes)

    def should_evolve(self) -> tuple[bool, str]:
        """成功率滑窗门控：是否进入"置信度评估"阶段。

        Returns:
            (allowed, reason)
        """
        if len(self._outcomes) < self._min_samples:
            return False, f"insufficient samples ({len(self._outcomes)}/{self._min_samples})"
        rate = self.recent_success_rate
        # 已全部成功 → 无可改进空间，抑制演进（省 token、避免过拟合噪声）
        if rate >= 1.0:
            return False, f"converged (recent_success_rate={rate:.2f})"
        return True, f"improvable (recent_success_rate={rate:.2f})"

    async def gate_evolution_record(self, record_text: str) -> bool:
        """置信度门控：评估一条候选演进记录是否值得入库。

        先用成功率滑窗粗筛，再用 LLM 自评置信度精筛。
        """
        allowed, reason = self.should_evolve()
        if not allowed:
            self._log_gate(False, reason, self.recent_success_rate)
            return False
        if self._judge is None:
            # 无评估模型 → 仅成功率门控
            self._log_gate(True, reason + " [confidence gate disabled]", self.recent_success_rate, 1.0)
            return True
        confidence = await self._estimate_confidence(record_text)
        ok = confidence >= self._min_confidence
        self._log_gate(
            ok,
            f"confidence={confidence:.2f} vs threshold={self._min_confidence}",
            self.recent_success_rate,
            confidence,
        )
        return ok

    async def _estimate_confidence(self, record_text: str) -> float:
        """LLM 自评：该经验对未来任务的可复用性 0-1。"""
        prompt = (
            "You are evaluating whether the following distilled experience is "
            "GENERAL and REUSABLE for FUTURE similar tasks (not a one-off fix).\n"
            'Reply with ONLY a JSON object: {"confidence": <float 0-1>, "reason": "<short>"}\n\n'
            "=== EXPERIENCE ===\n" + record_text[:8000]
        )
        try:
            resp = await self._judge.generate(messages=[{"role": "user", "content": prompt}])
            text = getattr(resp, "content", None) or (resp if isinstance(resp, str) else str(resp))
            import json as _json
            import re as _re

            m = _re.search(r"\{.*\}", text, _re.DOTALL)
            if m:
                payload = _json.loads(m.group(0))
                return float(payload.get("confidence", 0.0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ConfidenceGate] confidence estimate failed: %s", exc)
        return 0.0

    # ----------------------------------------------------------------- hooks
    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:  # noqa: D102
        """在每个任务迭代后，从 ctx 尝试提取成败信号并喂入滑窗。

        基准运行器也会显式调用 ``record_task_outcome``，两者互补。
        """
        success = getattr(ctx, "success", None)
        task_id = getattr(getattr(ctx, "inputs", None), "task_id", "") or "unknown"
        if isinstance(success, bool):
            self.record_task_outcome(str(task_id), success)

    # ------------------------------------------------------------------ util
    def _log_gate(self, allowed: bool, reason: str, rate: float, confidence: float = 0.0) -> None:
        entry = EvolutionGateLog(
            ts=time.time(),
            allowed=allowed,
            reason=reason,
            recent_success_rate=rate,
            confidence=confidence,
        )
        self._gate_logs.append(entry)
        with self._gate_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def export_gate_report(self) -> dict[str, Any]:  # noqa: D102
        allowed = sum(1 for g in self._gate_logs if g.allowed)
        return {
            "gate_decisions": len(self._gate_logs),
            "allowed": allowed,
            "suppressed": len(self._gate_logs) - allowed,
            "suppression_rate": (len(self._gate_logs) - allowed) / len(self._gate_logs) if self._gate_logs else 0.0,
            "final_recent_success_rate": self.recent_success_rate,
        }
