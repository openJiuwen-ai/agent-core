# coding: utf-8
"""准入证据包（ASG-SI / VaG 对齐，不改主实验数字）。

权威对照（只对齐契约，不复制其代码）：

- ASG-SI (github.com/kenhuangus/ASG-SI)：先重放再入库，证据包可第三方重建奖励
- VaG (arXiv:2608.05810)：三异构评审（schema / A-B replay / LLM）+ 边际增益子集
- SkillOpt (Microsoft, arXiv:2605.23904)：held-out 严格更高才采纳
- MUSE-Autoskill (arXiv:2605.27366)：有 tests/ 则沙箱必须通过才注册
- CoEvoSkills (arXiv:2604.01687, COLM 2026)：生成器 + 隔离代理验证器

本模块把一次准入拆成可重建的 JSONL。默认模式 **不要求** replay：
主基准 45×3×3 仍走 Gate1 余量门（when-gate）。``strict=True`` 才强制
执行向重放（对应 ``evolve_probe`` / utility k，live 上为诚实负结果）。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

VERIFIER_VERSION = "skillforge-vag-1.0"
SCHEMA_VERSION = "1.0"
MIN_RULE_CHARS = 8


def skill_hash(body: str) -> str:
    """技能正文的稳定短哈希，对应 ASG-SI ``skill_program_hash``。"""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class CriticResult:
    """单个异构评审的结果。status: pass | fail | skipped。"""

    name: str
    status: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceBundle:
    """ASG-SI 风格证据包 + VaG 三评审明细。"""

    schema_version: str = SCHEMA_VERSION
    verifier_version: str = VERIFIER_VERSION
    verified: bool = False
    decision: str = "reject"
    kind: str = "skill_admission"
    skill_name: str = ""
    skill_hash: str = ""
    critics: list[CriticResult] = field(default_factory=list)
    checked_at: str = ""
    num_tests: int = 0
    pass_rate: Optional[float] = None
    reason: str = ""
    written: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class ThreeCriticAdmission:
    """VaG 三评审组合器。

    - schema：结构合法性（最短长度），对应 VaG critic 1
    - when_gate：成功率滑窗余量，本作品 Gate 1（VaG 没有的 when 轴）
    - semantic：LLM 可复用性，对应 VaG critic 3 / 本作品 Gate 2
    - replay：held-out / 探测集 A-B，对应 VaG critic 2 / ASG-SI；默认 skipped

    ``strict=False``（默认）：缺失 replay 不拒绝，主数字不变。
    ``strict=True``：replay 必须提供且不低于基线。
    """

    def __init__(self, *, min_confidence: float = 0.6, slack: float = 0.0, strict: bool = False) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")
        self.min_confidence = min_confidence
        self.slack = slack
        self.strict = strict

    def evaluate(
        self,
        rule: str,
        *,
        when_allowed: bool,
        when_reason: str = "",
        confidence: Optional[float] = None,
        replayed: Optional[float] = None,
        baseline: Optional[float] = None,
        skill_name: str = "",
    ) -> EvidenceBundle:
        """对一条候选规则跑齐评审并给出 promote / reject。"""
        text = (rule or "").strip()
        critics: list[CriticResult] = []

        if not text:
            critics.append(CriticResult("schema", "skipped", "empty_rule"))
        elif len(text) < MIN_RULE_CHARS:
            critics.append(
                CriticResult(
                    "schema",
                    "fail",
                    "empty_or_short_rule",
                    {"n_chars": len(text), "min_chars": MIN_RULE_CHARS},
                )
            )
        else:
            critics.append(
                CriticResult(
                    "schema",
                    "pass",
                    "schema_ok",
                    {"n_chars": len(text)},
                )
            )

        critics.append(
            CriticResult(
                "when_gate",
                "pass" if when_allowed else "fail",
                when_reason or ("improvable" if when_allowed else "suppressed"),
            )
        )

        if confidence is None:
            critics.append(
                CriticResult(
                    "semantic",
                    "skipped",
                    "confidence_gate_disabled",
                )
            )
        else:
            ok = float(confidence) + 1e-12 >= self.min_confidence
            critics.append(
                CriticResult(
                    "semantic",
                    "pass" if ok else "fail",
                    f"confidence={float(confidence):.2f} vs {self.min_confidence}",
                    {"confidence": float(confidence), "threshold": self.min_confidence},
                )
            )

        if replayed is None:
            critics.append(
                CriticResult(
                    "replay",
                    "fail" if self.strict else "skipped",
                    "unevaluated_no_replay" if self.strict else "replay_not_required",
                )
            )
        else:
            base = -1.0 if baseline is None else float(baseline)
            r = float(replayed)
            ok = (base < 0.0) or (r + 1e-12 >= base - self.slack)
            critics.append(
                CriticResult(
                    "replay",
                    "pass" if ok else "fail",
                    "replay_ok" if ok else "replay_drop",
                    {"replayed": r, "baseline": base, "slack": self.slack},
                )
            )

        tested = [c for c in critics if c.status != "skipped"]
        n_pass = sum(1 for c in tested if c.status == "pass")
        failed = [c for c in tested if c.status == "fail"]
        verified = not failed
        reason = "all_critics_pass" if verified else ("fail:" + ",".join(c.name for c in failed))
        checked = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return EvidenceBundle(
            verified=verified,
            decision="promote" if verified else "reject",
            skill_name=skill_name,
            skill_hash=skill_hash(text),
            critics=critics,
            checked_at=checked,
            num_tests=len(tested),
            pass_rate=(n_pass / len(tested)) if tested else None,
            reason=reason,
        )


def emit_evidence(path: str | Path, bundle: EvidenceBundle) -> None:
    """追加一条证据包到 JSONL（ASG-SI reconstructible trail）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = bundle.to_dict()
    record["ts"] = time.time()
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
