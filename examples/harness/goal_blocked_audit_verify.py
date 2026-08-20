# coding: utf-8
"""阻塞审计端到端验证脚本（goal blocked audit E2E verification）。

用途：在真实模型驱动下验证 goal 阻塞审计（blocked audit）机制按设计工作。
构造一个“注定无法完成、且唯一根因”的 goal —— 读取一个不存在的 CSV 文件并产出报告，
真实模型会反复尝试替代手段，每次都因同一根因被评估为 blocked。

审计机制应表现为（blocked_threshold 默认 3）：
- 第 1 次 blocked → blocking_history_len=1, status 保持 ACTIVE（降级 continue，不放弃）
- 第 2 次 blocked → blocking_history_len=2, 仍 ACTIVE
- 第 3 次 blocked → blocking_history_len=3 ≥ threshold(3) → 最终 BLOCKED
- 不同根因的阻塞会重置计数（blocking_history 回到 1）

用法：
  cd /Users/lyh/CodeRepo/openJiuwen/agent-core
  uv run python examples/harness/goal_blocked_audit_verify.py

复用 ~/.openjiuwen/settings.json 里已配好的模型（DashScope/glm-5.2）。

关键观察日志（goal logger）:
  [GoalLifecycle] apply assessment: status=active goal=... blocking_history_len=2 blocked_threshold=3
  [GoalLifecycle] apply assessment: status=blocked goal=... blocking_history_len=3 blocked_threshold=3
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path.cwd()  # 必须在 agent-core 根目录下运行
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import copy  # noqa: E402

from loguru import logger as _loguru_logger  # noqa: E402

from openjiuwen.core.common.logging.log_config import configure_log_config  # noqa: E402
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG  # noqa: E402

_LOG_CONFIG = copy.deepcopy(DEFAULT_INNER_LOG_CONFIG)
if isinstance(_LOG_CONFIG, dict):
    _LOG_CONFIG.setdefault("loggers", {})["goal"] = {"level": "DEBUG"}
    _loggers = _LOG_CONFIG["loggers"]
    _sinks = _LOG_CONFIG.get("sinks")
    if isinstance(_sinks, dict):
        _loggers["goal"] = {"level": "DEBUG"}
        for _sink in _sinks.values():
            if isinstance(_sink, dict) and "level" in _sink:
                _sink["level"] = "DEBUG"
configure_log_config(_LOG_CONFIG)

_loguru_logger.remove()
_loguru_logger.add(
    sys.stderr,
    level="DEBUG",
    format=(
        "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <7}</level> | "
        "{message}"
    ),
    colorize=True,
)

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig  # noqa: E402
from openjiuwen.core.runner import Runner  # noqa: E402
from openjiuwen.core.single_agent.schema.agent_card import AgentCard  # noqa: E402
from openjiuwen.harness import create_deep_agent  # noqa: E402
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail  # noqa: E402

BLOCKING_OBJECTIVE = (
    "读取文件 /tmp/team_forecast_2026.csv，里面的数据是团队 2026 年各月销售预测。"
    "请按月份汇总各团队业绩，把汇总结果写成一份 Markdown 报告保存到当前工作目录，"
    "并报告各处关键数字（例如：哪些团队在哪个月份预测值最高）。"
    "只有真正拿到了这份 CSV 的数据、产出报告后才算完成，"
    "完成后调用 submit_goal_report 提交 status=complete。"
)


def _load_model_from_settings() -> Model:
    p = os.path.expanduser("~/.openjiuwen/settings.json")
    if not os.path.exists(p):
        raise SystemExit(f"找不到 {p}，请先配置模型。")
    d = json.load(open(p, encoding="utf-8"))
    if not d.get("apiKey"):
        raise SystemExit(f"{p} 里 apiKey 为空，请先配好模型。")
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=d.get("provider", "OpenAI"),
            api_key=d["apiKey"],
            api_base=d["apiBase"],
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model=d["model"], temperature=0.2, top_p=0.9),
    )


async def main() -> None:
    model = _load_model_from_settings()
    agent = create_deep_agent(
        model=model,
        card=AgentCard(name="goal_blocked_verify", description="blocked audit e2e verify"),
        workspace="/tmp/goal_blocked_verify_ws",
        language="cn",
        max_iterations=8,
        rails=[SysOperationRail()],
        auto_create_workspace=False,
        system_prompt="你是一个编程助手，擅长通过读写文件、执行命令来完成任务；遇到障碍如实报告。",
    )
    await Runner.start()
    stream = None
    try:
        await agent.ensure_initialized()
        await agent.start()
        stream = await agent.attach_output()
        if stream is None:
            raise SystemExit("attach_output 返回 None")

        goal = await agent.goal_manager.set(
            BLOCKING_OBJECTIVE,
            max_attempts=6,  # 留足次数观察 1/3→2/3→3/3
        )
        print(f"\n[goal set] id={goal.goal_id} status={goal.status.value}")
        print(f"[objective] {goal.objective}\n")
        print("---- 输出流（关键看 [GoalLifecycle] 行） ----")

        async for chunk in stream:
            payload = getattr(chunk, "payload", None)
            ctype = str(getattr(chunk, "type", "") or "")
            if ctype == "goal.updated" and isinstance(payload, dict):
                g = payload.get("goal") or {}
                asst = g.get("last_assessment") or {}
                print(
                    f"  [goal.updated] status={g.get('status', '?')} "
                    f"assessment={asst.get('status', '')} "
                    f"evidence={(asst.get('evidence') or '')[:100]!r}"
                )

        final = await agent.goal_manager.get()
        if final is not None:
            print("\n==== 最终 GoalRecord ====")
            print(f"final status = {final.status.value}")
            print(f"blocking_history = {list(final.blocking_history)}")
            print(f"last_stop_reason = {final.last_stop_reason}")
            print(json.dumps(final.to_dict(), ensure_ascii=False, indent=2)[:3000])
    finally:
        if stream is not None:
            await stream.close()
        await agent.stop()
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())