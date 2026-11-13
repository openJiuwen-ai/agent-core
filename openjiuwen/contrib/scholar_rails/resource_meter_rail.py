# coding: utf-8
"""ResourceMeter —— 对 openJiuwen Harness 的资源计量 Rail（框架贡献点 2）。

以 model 调用 / tool 调用 / task 迭代 / 整条 invoke 四个粒度，记录:
  - 输入/输出 token（含缓存命中明细，若 provider 返回）
  - 调用次数、耗时（秒）
  - 估算成本（按 DeepSeek 公开价目，可配置）

所有事件以 JSONL 追加写入 ``log_path``，保证"可追溯"（赛题资源报告硬性要求）。
运行结束后可用 ``summarize`` 生成聚合统计，直接喂给 resource_report.md。

该 Rail 只读回调上下文，不改写任何 prompt / tool 行为，因此对被观测的
Agent 透明（零侵入），符合 Harness 的 Rail 生命周期扩展范式。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from openjiuwen.harness.rails.base import DeepAgentRail

# DeepSeek 公开价目（人民币 / 百万 token），可通过构造参数覆盖
# 参考 https://api-docs.deepseek.com/quick_start/pricing （deepseek-chat，缓存命中/未命中区分）
_DEFAULT_PRICING = {
    "input_per_mtok": 2.0,  # ¥2 / 1M input（缓存未命中）
    "input_cached_per_mtok": 0.5,  # ¥0.5 / 1M input（缓存命中）
    "output_per_mtok": 8.0,  # ¥8 / 1M output
}


@dataclass
class MeterEvent:
    """单条计量事件。"""

    ts: float  # unix 时间戳
    kind: str  # model_call | tool_call | task_iteration | invoke
    name: str = ""  # 模型名 / 工具名 / "task_loop" / agent name
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    duration_sec: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class ResourceMeterRail(DeepAgentRail):
    """全链路资源计量 Rail。

    Args:
        log_path: JSONL 计量日志落盘路径。
        stage:    当前流水线阶段名（写入事件 extra，便于按 stage 聚合）。
        pricing:  覆盖默认定价。
    """

    priority = 5  # 尽量最先执行 before_*、最后执行 after_*，保证计时不被其他 rail 污染

    def __init__(
        self,
        log_path: str | Path,
        *,
        stage: str = "default",
        pricing: Optional[dict[str, float]] = None,
    ) -> None:
        super().__init__() if hasattr(super(), "__init__") else None
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._stage = stage
        self._pricing = {**_DEFAULT_PRICING, **(pricing or {})}
        self._lock = threading.Lock()
        # 以 id(ctx.inputs) 为 key 暂存起始时间（避免依赖具体 ctx 结构）
        self._t0: dict[int, float] = {}

    # ------------------------------------------------------------------ util
    def _emit(self, event: MeterEvent) -> None:
        event.extra.setdefault("stage", self._stage)
        line = json.dumps(asdict(event), ensure_ascii=False)
        with self._lock:
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _tick(self, ctx: Any) -> int:
        key = id(getattr(ctx, "inputs", ctx))
        self._t0[key] = time.perf_counter()
        return key

    def _tock(self, key: int) -> float:
        t0 = self._t0.pop(key, None)
        return (time.perf_counter() - t0) if t0 is not None else 0.0

    # --------------------------------------------------------------- hooks
    async def before_invoke(self, ctx: Any) -> None:  # noqa: D102
        self._tick(ctx)

    async def after_invoke(self, ctx: Any) -> None:  # noqa: D102
        key = id(getattr(ctx, "inputs", ctx))
        dur = self._tock(key)
        agent_name = getattr(getattr(ctx, "agent", None), "card", None)
        agent_name = getattr(agent_name, "name", "") if agent_name else ""
        self._emit(MeterEvent(ts=time.time(), kind="invoke", name=agent_name, duration_sec=dur))

    async def before_model_call(self, ctx: Any) -> None:  # noqa: D102
        self._tick(ctx)

    async def after_model_call(self, ctx: Any) -> None:  # noqa: D102
        key = id(getattr(ctx, "inputs", ctx))
        dur = self._tock(key)
        inp = out = cached = 0
        model_name = ""
        outputs = getattr(ctx, "outputs", None)
        usage = getattr(outputs, "usage", None) if outputs is not None else None
        if usage is not None:
            inp = int(getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0)
            out = int(getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0)
            cached = int(getattr(usage, "cached_tokens", 0) or 0)
        inputs = getattr(ctx, "inputs", None)
        model_name = getattr(inputs, "model", "") or getattr(getattr(ctx, "agent", None), "model_name", "") or ""
        self._emit(
            MeterEvent(
                ts=time.time(),
                kind="model_call",
                name=str(model_name),
                input_tokens=inp,
                output_tokens=out,
                cached_input_tokens=cached,
                duration_sec=dur,
            )
        )

    async def before_tool_call(self, ctx: Any) -> None:  # noqa: D102
        self._tick(ctx)

    async def after_tool_call(self, ctx: Any) -> None:  # noqa: D102
        key = id(getattr(ctx, "inputs", ctx))
        dur = self._tock(key)
        inputs = getattr(ctx, "inputs", None)
        tool = getattr(inputs, "tool_name", "") if inputs is not None else ""
        self._emit(MeterEvent(ts=time.time(), kind="tool_call", name=str(tool), duration_sec=dur))

    async def before_task_iteration(self, ctx: Any) -> None:  # noqa: D102
        self._tick(ctx)

    async def after_task_iteration(self, ctx: Any) -> None:  # noqa: D102
        key = id(getattr(ctx, "inputs", ctx))
        dur = self._tock(key)
        self._emit(MeterEvent(ts=time.time(), kind="task_iteration", name="task_loop", duration_sec=dur))

    # ------------------------------------------------------------ analysis
    def estimate_cost_cny(self, events: list[MeterEvent]) -> float:
        """按价目估算总成本（仅统计 model_call 事件）。"""
        return estimate_events_cost_cny(events, self._pricing)


def estimate_events_cost_cny(events: list[MeterEvent], pricing: dict[str, float]) -> float:
    """模块级成本估算，供 rail 实例与离线聚合（summarize）共用。"""
    cost = 0.0
    for e in events:
        if e.kind != "model_call":
            continue
        uncached_in = max(e.input_tokens - e.cached_input_tokens, 0)
        cost += (
            uncached_in / 1e6 * pricing["input_per_mtok"]
            + e.cached_input_tokens / 1e6 * pricing["input_cached_per_mtok"]
            + e.output_tokens / 1e6 * pricing["output_per_mtok"]
        )
    return cost


# --------------------------------------------------------------------------
# 离线聚合：从 JSONL 日志生成 resource_report 用的统计表
# --------------------------------------------------------------------------
def load_events(log_path: str | Path) -> list[MeterEvent]:
    events: list[MeterEvent] = []
    p = Path(log_path)
    if not p.exists():
        return events
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(MeterEvent(**json.loads(line)))
        except Exception:  # noqa: BLE001
            continue
    return events


def summarize(log_path: str | Path, *, pricing: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """聚合一份 JSONL 计量日志，返回 resource_report 所需的全部统计量。"""
    events = load_events(log_path)
    pricing = {**_DEFAULT_PRICING, **(pricing or {})}

    model_calls = [e for e in events if e.kind == "model_call"]
    tool_calls = [e for e in events if e.kind == "tool_call"]
    invokes = [e for e in events if e.kind == "invoke"]

    total_in = sum(e.input_tokens for e in model_calls)
    total_out = sum(e.output_tokens for e in model_calls)
    total_cached = sum(e.cached_input_tokens for e in model_calls)

    by_stage: dict[str, dict[str, int]] = {}
    for e in model_calls:
        stage = e.extra.get("stage", "default")
        slot = by_stage.setdefault(stage, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
        slot["input_tokens"] += e.input_tokens
        slot["output_tokens"] += e.output_tokens
        slot["calls"] += 1

    tool_counts: dict[str, int] = {}
    for e in tool_calls:
        tool_counts[e.name] = tool_counts.get(e.name, 0) + 1

    wall_time = max((e.ts for e in events), default=0) - min((e.ts for e in events), default=0)

    return {
        "event_count": len(events),
        "model_call_count": len(model_calls),
        "tool_call_count": len(tool_calls),
        "invoke_count": len(invokes),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cached_input_tokens": total_cached,
        "total_tokens": total_in + total_out,
        "estimated_cost_cny": round(estimate_events_cost_cny(events, pricing), 4),
        "wall_time_sec": round(wall_time, 2),
        "by_stage": by_stage,
        "tool_counts": tool_counts,
    }
