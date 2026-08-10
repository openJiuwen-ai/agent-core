# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""契约机制演示脚本（方案一 完成契约机制）。

演示两种契约来源 + 契约双注入效果：
  1. draft_contract：从模糊 objective 辅助生成契约（方式2）
  2. 手写契约：GoalContract(verification=..., boundaries=...)（方式1）
  3. goal_manager.set(objective, contract=...) 跑 goal，观察：
     - <goal_task> 里出现 <contract> 段（agent 看到）
     - assessor prompt 含 <contract>（日志可见）
     - assessor 按契约逐项验证

用法：
  cd /Users/lyh/CodeRepo/openJiuwen/agent-core
  uv run python examples/harness/goal_contract_demo.py
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---- SDK 日志（goal DEBUG + filter sink，复用 goal_388 的 initialize 修复）----
import copy  # noqa: E402

from loguru import logger as _loguru_logger  # noqa: E402

from openjiuwen.core.common.logging.log_config import configure_log_config  # noqa: E402
from openjiuwen.core.common.logging.loguru.constant import (  # noqa: E402
    DEFAULT_INNER_LOG_CONFIG,
)
from openjiuwen.core.common.logging.manager import LogManager  # noqa: E402

_LOG_LEVEL = "DEBUG"
_LOG_CONFIG = copy.deepcopy(DEFAULT_INNER_LOG_CONFIG)
if isinstance(_LOG_CONFIG, dict):
    _loggers = _LOG_CONFIG.setdefault("loggers", {})
    _loggers["goal"] = {"level": _LOG_LEVEL}
    _sinks = _LOG_CONFIG.get("sinks")
    if isinstance(_sinks, dict):
        for _sink in _sinks.values():
            if isinstance(_sink, dict) and "level" in _sink:
                _sink["level"] = _LOG_LEVEL
configure_log_config(_LOG_CONFIG)
LogManager.initialize()

_DROP_PREFIXES = (
    "Registered ", "Added GLOBAL tag", "add resource succeed",
    "Create a new agent checkpointer", "Begin to restore", "Succeed to restore",
    "Begin to save agent checkpoint", "Succeed to save agent checkpoint",
    "Before request chat model", "Before parse", "Before create openai",
    "OpenAI API response received", "Before parse content", "Before parse response",
    "Start to write file", "End to write file", "Start to read file", "End to read file",
    "Start to execute cmd", "End to execute cmd",
    "[BaseSecurityRail]", "[PromptAttachmentManager]", "[ImageModalityProbe]",
    "TaskScheduler", "Controller started", "No saved state", "cancelling",
    "schedule loop", "schedule end", "cancelled", "stopped", "Begin to start runner",
    "Succeed to start runner", "CoroutineTaskManager", "Stopping read-write",
    "Started read-write", "Task persistence", "Published",
    "Executing task ", "running_tasks",
)
_KEEP_PREFIXES = (
    "[DeepAgent]", "[GoalEvaluator]", "[GoalLifecycle]", "[GoalReportSink]",
    "Executing tool:", "[LLM]", "ReAct iteration", "[OuterLoop]",
    "TaskCompletionRail:", "slow callback", "transcript assessor",
    "[RailChain]",
)


def _keep(record) -> bool:
    if record["extra"].get("log_type") == "goal":
        return True
    msg = record["message"]
    for p in _DROP_PREFIXES:
        if msg.startswith(p):
            return False
    for p in _KEEP_PREFIXES:
        if msg.startswith(p):
            return True
    return False


_loguru_logger.remove()
_loguru_logger.add(
    sys.stderr,
    level="DEBUG",
    filter=_keep,
    format=(
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level: <7}</level> | "
        "<cyan>{extra[log_type]}</cyan> | "
        "{file}:{line} | {message}"
    ),
    colorize=True,
)

from openjiuwen.core.foundation.llm import (  # noqa: E402
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner  # noqa: E402
from openjiuwen.core.single_agent.schema.agent_card import AgentCard  # noqa: E402
from openjiuwen.harness import create_deep_agent  # noqa: E402
from openjiuwen.harness.goal import GoalContract, draft_contract  # noqa: E402
from openjiuwen.harness.goal.schema import GoalStatus  # noqa: E402
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail  # noqa: E402

# 388 评测任务的源数据文件（hash 前缀原名 → 干净名），不含 generate_pptx.py（答案）
_DATA_SRC_DIR = Path("/Users/lyh/CodeRepo/openJiuwen/docs/388/data")
_SOURCE_FILES = {
    "e44f4e39901a4232_meeting_minutes_Q1_review.txt": "meeting_minutes_Q1_review.txt",
    "f73004f5a1e4bdd5_team_performance_KPI.xlsx": "team_performance_KPI.xlsx",
    "3ca1976219679ec3_competitor_analysis.json": "competitor_analysis.json",
}


def _load_model_from_settings() -> Model:
    p = os.path.expanduser("~/.openjiuwen/settings.json")
    if not os.path.exists(p):
        raise SystemExit(f"找不到 {p}。请先 `uv run openjiuwen` 配置模型。")
    d = json.load(open(p, encoding="utf-8"))
    if not d.get("apiKey"):
        raise SystemExit(f"{p} 里 apiKey 为空。")
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=d.get("provider", "OpenAI"),
            api_key=d["apiKey"],
            api_base=d["apiBase"],
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(
            model=d["model"],
            temperature=0.2,
            top_p=0.9,
        ),
    )


class _StreamRenderer:
    """聚合逐 token chunk，只打印 goal.updated / llm_output 段 / answer。"""

    def __init__(self) -> None:
        self._buf: list[str] = []

    @staticmethod
    def _type(chunk) -> str:
        t = getattr(chunk, "type", None)
        if t is None and isinstance(chunk, dict):
            t = chunk.get("type") or chunk.get("event_type")
        return str(t) if t else ""

    @staticmethod
    def _payload(chunk) -> dict:
        p = getattr(chunk, "payload", None)
        if isinstance(p, dict):
            return p
        if isinstance(chunk, dict):
            p = chunk.get("payload")
            return p if isinstance(p, dict) else {}
        return {}

    def feed(self, chunk) -> list[str]:
        t = self._type(chunk)
        if t == "llm_reasoning":
            return []
        if t == "llm_output":
            c = self._payload(chunk).get("content")
            if c:
                self._buf.append(str(c))
            return []
        lines = self._flush()
        if t == "goal.updated":
            goal = self._payload(chunk).get("goal") or {}
            status = goal.get("status", "?")
            assessment = goal.get("last_assessment") or {}
            asst = assessment.get("status", "")
            ev = (assessment.get("evidence") or "")[:120]
            lines.append(f"  [goal.updated] status={status} assessment={asst} evidence={ev!r}")
        elif t == "answer":
            out = self._payload(chunk).get("output") or ""
            lines.append(f"  [answer] {str(out)[:240]}")
        return lines

    def flush_final(self) -> list[str]:
        return self._flush()

    def _flush(self) -> list[str]:
        if not self._buf:
            return []
        text = "".join(self._buf)
        self._buf = []
        return [f"  [llm_output] {text[:240]}"]


def _prepare_workspace(workspace: Path) -> None:
    """清空 + 重建 workspace，复制 3 个 388 源数据文件（不含 generate_pptx.py 答案）。"""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    if not _DATA_SRC_DIR.is_dir():
        raise SystemExit(f"找不到 388 源数据目录 {_DATA_SRC_DIR}")
    for src_name, dst_name in _SOURCE_FILES.items():
        src = _DATA_SRC_DIR / src_name
        if not src.is_file():
            raise SystemExit(f"找不到源文件 {src}")
        shutil.copy(src, workspace / dst_name)
    print(f"[workspace] {workspace}")
    print(f"[source files] {list(_SOURCE_FILES.values())}")


async def main() -> None:
    workspace = Path("/tmp/goal_contract_ws")
    _prepare_workspace(workspace)

    model = _load_model_from_settings()

    # 388 评测任务目标（模糊自然语言，来自 docs/388/metadata.json 的 task）
    objective = (
        "基于当前工作目录下的三个数据文件（meeting_minutes_Q1_review.txt "
        "战略复盘会议纪要、team_performance_KPI.xlsx 团队绩效与月度指标记录、"
        "competitor_analysis.json 竞品分析数据），整合数据、提炼管理洞察，"
        "生成一份专业的 PowerPoint 战略汇报文件 NovaMind_Q1复盘与Q2战略规划.pptx，"
        "保存到当前工作目录。PPT 约 10 页，含封面/目录/Q1核心指标/趋势/用户研究/"
        "竞品/Q2 OKR/路线图/行动项/结语。PPT 中所有数据必须与源文件一致。"
    )

    # 方式2: draft_contract 从模糊目标辅助生成契约
    print("\n==== 方式2: draft_contract 从 388 模糊目标生成契约 ====")
    print(f"[objective] {objective[:80]}...")
    drafted = await draft_contract(objective, model, "cn")
    print("[draft_contract 生成契约]")
    print(json.dumps(drafted.to_dict(), ensure_ascii=False, indent=2))
    if drafted.is_empty():
        print("[draft 生成空契约，set 后退化为无契约行为]")

    agent = create_deep_agent(
        model=model,
        card=AgentCard(name="goal_contract_388", description="388 contract demo"),
        workspace=str(workspace),
        language="cn",
        max_iterations=20,
        rails=[SysOperationRail()],
        auto_create_workspace=False,
        system_prompt=(
            "你是一个产品经理/数据分析助手，擅长读取异构数据文件（txt/xlsx/json）、"
            "整合数据、用 python-pptx 生成专业 PPT。完成任务后调用 submit_goal_report。"
        ),
    )

    await Runner.start()
    stream = None
    try:
        await agent.ensure_initialized()
        await agent.start()
        stream = await agent.attach_output()
        if stream is None:
            raise SystemExit("attach_output 返回 None（输出流已被占用）")

        goal = await agent.goal_manager.set(
            objective,
            max_attempts=3,
            contract=drafted,  # 用 draft_contract 生成的契约
        )
        print(f"\n[goal set] id={goal.goal_id} status={goal.status.value}")
        print(f"[record.contract] {goal.contract.to_dict() if goal.contract else None}")
        print("\n---- output stream ----")

        renderer = _StreamRenderer()
        async for chunk in stream:
            for _line in renderer.feed(chunk):
                print(_line)
        for _line in renderer.flush_final():
            print(_line)

        final = await agent.goal_manager.get()
        if final is not None:
            print("\n==== 最终 GoalRecord ====")
            print(json.dumps(final.to_dict(), ensure_ascii=False, indent=2))
        else:
            print("\n[goal 已被 clear 或不存在]")
    finally:
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.close()
        with contextlib.suppress(Exception):
            await agent.stop()
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
