# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""goal 端到端调试脚本。

走 DeepAgent interaction 模式跑通 goal 闭环（与 jiuwenswarm ``/goal set`` 同一套
驱动），配 SDK 内部 DEBUG 日志 + 详细 chunk 打印，用来反复验证 goal 的逻辑和效果：
  set → attempt → submit_goal_report → apply_assessment → 终态

用法：
  cd /Users/lyh/CodeRepo/openJiuwen/agent-core
  uv run python examples/harness/goal_debug.py

复用 ~/.openjiuwen/settings.json 里已配好的模型（DashScope/glm-5.2）。
改 goal 任何代码后重跑此脚本即可对比效果。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from pathlib import Path

# 让脚本从任意位置都能 import openjiuwen
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---- SDK 日志配置（必须在 import openjiuwen.* 之前，避免日志分裂）----
# 降到 DEBUG 以看到 supervisor dequeue、GoalEvaluator 每步、GoalLifecycle、
# GoalReportSink 等全过程。日志量较大时把下面 _LOG_LEVEL 改成 "INFO"。
import copy  # noqa: E402

from loguru import logger as _loguru_logger  # noqa: E402

from openjiuwen.core.common.logging.log_config import configure_log_config  # noqa: E402
from openjiuwen.core.common.logging.loguru.constant import (  # noqa: E402
    DEFAULT_INNER_LOG_CONFIG,
)

_LOG_LEVEL = "DEBUG"
_LOG_CONFIG = copy.deepcopy(DEFAULT_INNER_LOG_CONFIG)
if isinstance(_LOG_CONFIG, dict):
    # 只把 goal logger 降到 DEBUG（让 transcript assessor raw response 等
    # 调试日志可见），其他 namespace 保持 INFO，避免全量 debug 日志爆炸。
    _loggers = _LOG_CONFIG.setdefault("loggers", {})
    _loggers["goal"] = {"level": _LOG_LEVEL}
    # sink 级别也要降到 DEBUG，否则 logger 产出的 debug 会被 sink 再滤一次。
    _sinks = _LOG_CONFIG.get("sinks")
    if isinstance(_sinks, dict):
        for _sink in _sinks.values():
            if isinstance(_sink, dict) and "level" in _sink:
                _sink["level"] = _LOG_LEVEL
configure_log_config(_LOG_CONFIG)

# 调试脚本专用：用一个带白/黑名单的 sink 替换默认 sink，砍掉启动注册、
# checkpointer、fs_operation、security、model_clients 等噪音行，只留 goal
# + DeepAgent + react_agent + 工具调用 + 评估链路的关键行。库代码勿直接用
# loguru（见 .claude/rules/logging.md）；这里只是 examples 脚本本地过滤。
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
    """从 ~/.openjiuwen/settings.json 读模型配置（CLI 已配好的）。"""
    p = os.path.expanduser("~/.openjiuwen/settings.json")
    if not os.path.exists(p):
        raise SystemExit(
            f"找不到 {p}。请先 `uv run openjiuwen` 走配置向导，或手动建 settings.json。"
        )
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
        model_config=ModelRequestConfig(
            model=d["model"],
            temperature=0.2,
            top_p=0.9,
        ),
    )


class _StreamRenderer:
    """聚合 output stream 的逐 token chunk，跳过噪音，输出可读的逐事件行。

    - llm_reasoning / llm_usage / tracer / controller_output → 跳过
    - llm_output → 累积成一段，遇到非 llm_output 时 flush 成一行
    - goal.updated → 展开 status / assessment / evidence
    - answer → 打印最终输出
    """

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
        # 逐 token 思考过程：跳过（噪音大头）
        if t == "llm_reasoning":
            return []
        # 模型输出 token：累积，不立即打印
        if t == "llm_output":
            c = self._payload(chunk).get("content")
            if c:
                self._buf.append(str(c))
            return []
        # 遇到非 llm_output：先把累积的输出 flush 成一行
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
        # 其余（llm_usage / tracer_agent / controller_output / 未知）→ 跳过
        return lines

    def flush_final(self) -> list[str]:
        return self._flush()

    def _flush(self) -> list[str]:
        if not self._buf:
            return []
        text = "".join(self._buf)
        self._buf = []
        return [f"  [llm_output] {text[:240]}"]


async def main() -> None:
    workspace = Path("/tmp/goal_debug_ws")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    model = _load_model_from_settings()

    # SysOperationRail 注入 bash / read_file / write_file / edit_file / glob /
    # list_dir / grep / code，让 goal 任务有工具可用。
    agent = create_deep_agent(
        model=model,
        card=AgentCard(name="goal_debug", description="goal e2e debug"),
        workspace=str(workspace),
        language="cn",
        max_iterations=8,
        rails=[SysOperationRail()],
        auto_create_workspace=False,
        system_prompt="你是一个编程助手，擅长通过读写文件、执行命令来完成任务。",
    )

    await Runner.start()
    stream = None
    try:
        await agent.ensure_initialized()
        # 进入 interaction 模式：自动装配 GoalManager + TaskCompletionRail，
        # 注册 submit_goal_report / get_current_goal 工具，启动 supervisor。
        await agent.start()
        # 必须先 attach_output，goal_manager.set 才会排队首个 goal round。
        stream = await agent.attach_output()
        if stream is None:
            raise SystemExit("attach_output 返回 None（输出流已被占用）")

        goal = await agent.goal_manager.set(
            "生成一个 PowerPoint 文件 hello.pptx（用 python-pptx 库）："
            "第 1 页标题写 hello goal，副标题写 agent ppt test，"
            "保存到当前工作目录。然后确认文件存在且可用 python-pptx 正常打开"
            "（用 bash 验证：python -c \"from pptx import Presentation; "
            "p=Presentation('hello.pptx'); print(len(p.slides.slides))\"）。"
            "完成后调用 submit_goal_report 提交 status=complete。",
            max_attempts=3,
        )
        print(f"\n[goal set] id={goal.goal_id} status={goal.status.value}")
        print(f"[objective] {goal.objective}\n")
        print("---- output stream ----")

        # 读输出流。goal 到 COMPLETED/BLOCKED 后 supervisor 无法重排下一轮，
        # _close_idle_output_if_finished 入队哨兵，async for 自然终止。
        # 用 _StreamRenderer 聚合逐 token 噪音，只打印 goal.updated / llm_output 段 / answer。
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
