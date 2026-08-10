# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""388 PPT 战略汇报 goal 任务（复杂任务端到端）。

基于 docs/388 的评测任务定义：3 个异构数据文件（会议纪要 txt + 团队绩效 xlsx +
竞品分析 json）→ 整合 → 生成专业 PPT 战略汇报。任务复杂（读 3 文件 + 数据整合 +
10 页 PPT），max_iterations=20，预期触发多 attempt / continue / 甚至 BLOCKED，
用来压出 goal 完整行为。

源数据文件复制自 docs/388/data/，**不含 generate_pptx.py（答案）**，让 agent
自己写代码整合 + 生成 PPT。

用法：
  cd /Users/lyh/CodeRepo/openJiuwen/agent-core
  uv run python examples/harness/goal_388_pptx.py

复用 ~/.openjiuwen/settings.json 的模型配置。
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

# 388 评测任务的源数据文件（hash 前缀原名）→ 复制到 workspace 用的干净名。
_DATA_SRC_DIR = Path("/Users/lyh/CodeRepo/openJiuwen/docs/388/data")
_SOURCE_FILES = {
    "e44f4e39901a4232_meeting_minutes_Q1_review.txt": "meeting_minutes_Q1_review.txt",
    "f73004f5a1e4bdd5_team_performance_KPI.xlsx": "team_performance_KPI.xlsx",
    "3ca1976219679ec3_competitor_analysis.json": "competitor_analysis.json",
}

# ---- SDK 日志配置（必须在 import openjiuwen.* 之前）----
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
# 关键：强制 initialize 完成（用上面的 config 建 sink），再换成带白/黑名单的
# filter sink。否则后续 LazyLogger 首次使用触发的 initialize() 会覆盖我们的
# filter sink。库代码勿直接用 loguru；这里只是 examples 脚本本地过滤。
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
from openjiuwen.harness.goal.schema import GoalStatus  # noqa: E402
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail  # noqa: E402


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
    """清空 + 重建 workspace，复制 3 个源数据文件（不含 generate_pptx.py 答案）。"""
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
    workspace = Path("/tmp/goal_388_ws")
    _prepare_workspace(workspace)

    model = _load_model_from_settings()

    agent = create_deep_agent(
        model=model,
        card=AgentCard(name="goal_388", description="388 pptx strategy goal"),
        workspace=str(workspace),
        language="cn",
        max_iterations=20,
        rails=[SysOperationRail()],
        auto_create_workspace=False,
        system_prompt=(
            "你是一个产品经理 / 数据分析助手，擅长读取异构数据文件（txt/xlsx/json）、"
            "整合数据、提炼洞察，并用 python-pptx 生成专业 PowerPoint 汇报。"
            "完成任务后必须调用 submit_goal_report 提交完成度报告。"
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
            "基于当前工作目录下的三个数据文件整合生成一份专业的 PowerPoint 战略汇报：\n"
            "1. meeting_minutes_Q1_review.txt（战略复盘会议纪要）\n"
            "2. team_performance_KPI.xlsx（团队绩效与月度指标记录）\n"
            "3. competitor_analysis.json（竞品分析数据）\n\n"
            "要求：读取并理解这三个文件的数据，整合提炼管理洞察，"
            "生成 PowerPoint 文件 NovaMind_Q1复盘与Q2战略规划.pptx，"
            "保存到当前工作目录。PPT 应包含约 10 页：封面、目录、"
            "Q1核心指标全景、趋势图、用户研究洞察、竞品格局评估、"
            "Q2 OKR 战略规划、月度路线图、行动项追踪表、结语。"
            "PPT 中所有数据必须与三个源文件保持一致，不要编造。"
            "完成后调用 submit_goal_report 提交 status=complete。",
            max_attempts=3,
        )
        print(f"\n[goal set] id={goal.goal_id} status={goal.status.value}")
        print(f"[objective] {goal.objective}\n")
        print("---- output stream ----")

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
