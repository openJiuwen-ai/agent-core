# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-side extraction agent: distill team tasks/messages into ``team-memory`` files."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, List, Optional

from openjiuwen.core.common.logging import memory_logger as logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.database import TeamDatabase
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.sys_operation.sys_operation import SysOperation


TASK_CONTENT_PREVIEW_MAX = 2000
MESSAGE_CONTENT_PREVIEW_MAX = 1000
EXTRACTION_AGENT_MAX_ITERATIONS = 5
TEAM_MEMORY_FILENAME = "TEAM_MEMORY.md"
TEAM_MEMORY_MAX_READ_LINES = 200


EXTRACTION_AGENT_PROMPT = f"""\
你是团队记忆提取 agent。你的工作目录是团队记忆目录，里面可能已有之前提取的记忆文件。

## 你的任务

分析提供的团队协作记录（任务和消息），从中提炼出对未来团队协作有价值的持久记忆，写入记忆文件。

## 工作流程

1. 先用 Read 读取已有的记忆文件（如 {TEAM_MEMORY_FILENAME}），了解已记录的内容
2. 分析新的协作记录，判断哪些信息值得记忆
3. 用 Write/Edit 更新记忆文件：
   - 更新已有记忆条目（如果新信息补充或修正了旧内容）
   - 添加新的记忆条目
   - 删除已过时的条目
   - 合并重复内容

## 提取什么

1. **[decision] 团队决策**: 为什么选择了某个方案、拒绝了哪些替代方案、关键权衡
2. **[lesson] 经验教训**: 什么做法有效、什么导致了返工或问题、值得复用的模式
3. **[member] 成员特长**: 谁擅长什么、谁负责哪个领域、协作模式
4. **[context] 项目背景**: 非代码可推导的业务约束、截止日期、利益相关方要求

## 不要提取什么

- 代码细节、具体文件路径、函数名（可从代码库获取）
- 临时状态、进行中的调试过程
- 原始对话内容的复述（提取的是洞察，不是摘要）
- 任何敏感信息（密钥、凭证、个人隐私）

## 记忆文件格式

{TEAM_MEMORY_FILENAME} 中每条记忆用三级标题 + 类型标签，示例：

    ### [decision] 选择了方案 A 而非 B
    原因是... 权衡是...

    ### [lesson] 并行任务需要先对齐接口
    上次因为没对齐导致返工 2 天...

保持 {TEAM_MEMORY_FILENAME} 在 {TEAM_MEMORY_MAX_READ_LINES} 行以内。超出时合并或删除最旧的条目。
如果没有值得提取的新信息，不要修改文件。
"""


def _build_extraction_context(
    tasks: List[Any],
    messages: List[Any],
    tz_offset_hours: float,
) -> str:
    """Assemble DB records into user messages for the extract agent."""
    tz = timezone(timedelta(hours=tz_offset_hours))
    parts = ["# 本轮团队协作记录\n"]

    if tasks:
        parts.append("## 任务记录\n")
        for t in tasks:
            assignee = t.assignee or "未分配"
            parts.append(f"### {t.title} [{t.status}] -> {assignee}")
            if t.content:
                parts.append(t.content[:TASK_CONTENT_PREVIEW_MAX])
            parts.append("")

    if messages:
        parts.append("## 团队对话\n")
        sorted_msgs = sorted(messages, key=lambda m: m.timestamp)
        for m in sorted_msgs:
            ts = datetime.fromtimestamp(m.timestamp / 1000, tz=tz).strftime("%m-%d %H:%M")
            direction = "-> 全体" if m.broadcast else f"-> {m.to_member_name or '?'}"
            parts.append(
                f"[{ts}] {m.from_member_name} {direction}: "
                f"{m.content[:MESSAGE_CONTENT_PREVIEW_MAX]}"
            )
        parts.append("")

    return "\n".join(parts)


def _safe_memory_path(team_memory_dir: str, path: str) -> Optional[str]:
    if ".." in path or path.startswith("/") or path.startswith("\\"):
        return None
    return os.path.join(team_memory_dir, os.path.basename(path))


def _read_local_file(full_path: str) -> str:
    if not os.path.exists(full_path):
        return ""
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()


def _write_local_file_atomic(full_path: str, content: str) -> None:
    parent = os.path.dirname(full_path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            suffix=".tmp",
            prefix="team_memory_extract_",
            delete=False,
        ) as f:
            tmp_path = f.name
            f.write(content)
        os.replace(tmp_path, full_path)
        tmp_path = None
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _create_extraction_tools(
    team_memory_dir: str,
    *,
    team_name: str = "",
) -> List[Any]:
    """Create tools that read/write ``team-memory/`` via local FS.

    Intentionally independent of harness ``sys_operation`` so a background
    extract can finish after ``harness.dispose()`` has unregistered that handle.
    """
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

    prefix = f"extract.{team_name}" if team_name else "extract"

    async def _read_file(path: str) -> dict:
        full_path = _safe_memory_path(team_memory_dir, path)
        if full_path is None:
            return {"error": "Invalid path"}
        try:
            content = _read_local_file(full_path)
            return {"content": content, "path": full_path}
        except Exception:
            return {"content": "", "path": full_path, "note": "file not found"}

    async def _write_file(path: str, content: str) -> dict:
        full_path = _safe_memory_path(team_memory_dir, path)
        if full_path is None:
            return {"error": "Invalid path"}
        try:
            _write_local_file_atomic(full_path, content)
            return {"success": True, "path": full_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _list_files() -> dict:
        try:
            if not os.path.isdir(team_memory_dir):
                return {"files": []}
            files = [
                name
                for name in os.listdir(team_memory_dir)
                if os.path.isfile(os.path.join(team_memory_dir, name))
            ]
            return {"files": files}
        except Exception:
            return {"files": []}

    return [
        LocalFunction(
            card=ToolCard(
                id=f"{prefix}.read",
                name="read_memory_file",
                description="读取团队记忆目录下的文件",
                input_params={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            func=_read_file,
        ),
        LocalFunction(
            card=ToolCard(
                id=f"{prefix}.write",
                name="write_memory_file",
                description="写入团队记忆目录下的文件（覆盖）",
                input_params={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            func=_write_file,
        ),
        LocalFunction(
            card=ToolCard(
                id=f"{prefix}.list",
                name="list_memory_files",
                description="列出团队记忆目录下的所有文件",
                input_params={"type": "object", "properties": {}},
            ),
            func=_list_files,
        ),
    ]


async def snapshot_team_extraction_context(
    *,
    team_name: str,
    db: "TeamDatabase",
    task_manager: "TeamTaskManager",
    tz_offset_hours: float = 8.0,
) -> Optional[str]:
    """Read tasks/messages while the session DB is still bound.

    Returns ``None`` when there is nothing to extract. Callers that schedule
    background generation must finish this step before ``release_session``.
    """
    if db is None or task_manager is None:
        return None

    tasks = await task_manager.list_tasks()
    messages = await db.message.get_team_messages(team_name)
    if not tasks and not messages:
        return None
    return _build_extraction_context(tasks, messages, tz_offset_hours)


async def run_team_memory_extraction(
    *,
    team_name: str,
    team_memory_dir: str,
    context: str,
    model: "Model",
) -> None:
    """Run the extraction agent against an already-snapshotted context string."""
    if not team_memory_dir or model is None or not context:
        return

    os.makedirs(team_memory_dir, exist_ok=True)

    from openjiuwen.harness.factory import create_deep_agent
    from openjiuwen.core.runner.runner import Runner

    extraction_tools = _create_extraction_tools(team_memory_dir, team_name=team_name)
    agent = create_deep_agent(
        model,
        system_prompt=EXTRACTION_AGENT_PROMPT,
        tools=extraction_tools,
        max_iterations=EXTRACTION_AGENT_MAX_ITERATIONS,
        enable_task_loop=False,
    )

    await Runner.run_agent(
        agent,
        {"query": f"请分析以下团队 {team_name} 的协作记录并提取记忆：\n\n{context}"},
    )
    logger.info(f"[extractor] Extraction agent completed for {team_name}")


async def extract_team_memories(
    *,
    team_name: str,
    db: "TeamDatabase",
    task_manager: "TeamTaskManager",
    team_memory_dir: str,
    sys_operation: Optional["SysOperation"] = None,
    model: Optional["Model"] = None,
    tz_offset_hours: float = 8.0,
) -> None:
    """Forked Agent 提炼团队记忆（同步完整路径，供测试与兼容调用）。

    ``sys_operation`` 保留入参以兼容旧调用方，写盘已改为本地 FS，不再使用它。
    ``model`` 为 None 时不执行（无 LLM 无法提炼）。
    """
    del sys_operation  # kept for API compatibility; tools use local FS
    if None in (team_memory_dir, model, db, task_manager):
        return

    try:
        context = await snapshot_team_extraction_context(
            team_name=team_name,
            db=db,
            task_manager=task_manager,
            tz_offset_hours=tz_offset_hours,
        )
        if not context:
            return
        await run_team_memory_extraction(
            team_name=team_name,
            team_memory_dir=team_memory_dir,
            context=context,
            model=model,
        )
    except Exception as e:
        logger.warning(f"[extractor] extract_team_memories failed: {e}")


__all__ = [
    "EXTRACTION_AGENT_MAX_ITERATIONS",
    "EXTRACTION_AGENT_PROMPT",
    "MESSAGE_CONTENT_PREVIEW_MAX",
    "TASK_CONTENT_PREVIEW_MAX",
    "extract_team_memories",
    "run_team_memory_extraction",
    "snapshot_team_extraction_context",
]
