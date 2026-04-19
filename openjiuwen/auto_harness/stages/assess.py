# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Assess 阶段 — 评估当前状态 + 竞品差距分析。

合并原 ``assessment/assessor.py`` (AssessmentAgent) 和
``assessment/gap_analyzer.py`` (GapAnalyzer) 的逻辑。
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    List,
)

from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
    parse_gaps,
)
from openjiuwen.auto_harness.stages.base import (
    SessionStage,
)
from openjiuwen.auto_harness.contexts import (
    SessionContext,
)
from openjiuwen.auto_harness.schema import (
    AssessmentArtifact,
    AutoHarnessConfig,
    Experience,
    Gap,
    StageResult,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )

logger = logging.getLogger(__name__)


def _write_debug_artifact(
    runs_dir: str,
    filename: str,
    content: str,
) -> str:
    path = Path(runs_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


# ----------------------------------------------------------
# public API
# ----------------------------------------------------------


async def _run_assess_with_fallback(
    config: AutoHarnessConfig,
    experience_store: "ExperienceStore",
) -> str:
    """Generate the assess report with fallback behavior."""
    try:
        return await _assess_with_agent(
            config, experience_store
        )
    except Exception:
        logger.warning(
            "Agent assess failed, using fallback",
            exc_info=True,
        )
        return await _fallback_assess(
            config, experience_store
        )


async def run_assess_stream(
    config: AutoHarnessConfig,
    experience_store: "ExperienceStore",
) -> AsyncIterator[Any]:
    """流式评估，yield OutputSchema chunks。

    Args:
        config: Auto Harness 配置。
        experience_store: ExperienceStore 实例。

    Yields:
        OutputSchema chunks from DeepAgent.
    """
    from openjiuwen.auto_harness.agents import (
        create_assess_agent,
    )

    agent = create_assess_agent(config)
    query = await _build_query(
        config, experience_store
    )
    async for chunk in agent.stream({"query": query}):
        yield chunk


async def run_gap_analysis(
    config: AutoHarnessConfig,
    competitor: str,
    harness_state: str,
) -> List[Gap]:
    """用 DeepAgent 分析与竞品的差距。

    Args:
        config: Auto Harness 配置。
        competitor: 竞品框架名称。
        harness_state: 当前 harness 评估文本。

    Returns:
        按优先级排序的 Gap 列表。
    """
    try:
        return await _analyze_gaps_with_agent(
            config, competitor, harness_state,
        )
    except Exception:
        logger.warning(
            "Agent gap analysis failed",
            exc_info=True,
        )
        return []


class AssessStage(SessionStage):
    """Assess the repository state for the current session."""

    name = "assess"
    description = "Assess current repository state."
    produces = ["assessment"]

    async def stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        assessment = ""
        yield ctx.message("[Phase A1] 评估当前状态...")
        async for chunk in run_assess_stream(
            ctx.orchestrator.config,
            ctx.orchestrator.experience_store,
        ):
            text = extract_text(chunk)
            if text:
                assessment += text
            yield chunk
        if not assessment:
            assessment = await _run_assess_with_fallback(
                ctx.orchestrator.config,
                ctx.orchestrator.experience_store,
            )
        artifacts = {}
        if assessment.strip():
            _write_debug_artifact(
                ctx.orchestrator.config.runs_dir,
                "latest_assessment.md",
                assessment,
            )
            artifacts["assessment"] = AssessmentArtifact(
                report=assessment
            )
        yield StageResult(
            artifacts=artifacts,
        )


# ----------------------------------------------------------
# internal: assess
# ----------------------------------------------------------


async def _build_query(
    config: AutoHarnessConfig,
    experience_store: Any,
) -> str:
    """组装 assess agent 的 query。"""
    recent = await experience_store.list_recent(limit=10)
    experiences_text = _format_experiences(recent)
    today = datetime.date.today().isoformat()
    workspace = config.workspace or "."
    check_strategy = await _detect_python_check_strategy(
        workspace
    )
    return (
        f"当前日期: {today}\n"
        f"工作目录: {workspace}\n\n"
        f"本轮目标: {config.optimization_goal or '无'}\n\n"
        f"重点竞品: {config.competitor or '无'}\n\n"
        f"Python 检查策略建议:\n"
        f"{check_strategy}\n\n"
        f"近期经验:\n{experiences_text}\n\n"
        "请按照你的系统提示执行评估任务。"
        "优先遵循给出的 Python 检查策略建议，"
        "不要臆测 allowlist 或 Makefile 行为。"
        "如果提供了本轮目标，请围绕该目标缩小评估范围。"
        "如果提供了重点竞品，请把差距分析作为评估重点。"
    )


async def _run_git_lines(
    workspace: str,
    *args: str,
) -> list[str]:
    """Run a git command and return stripped non-empty output lines."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    lines: list[str] = []
    decoded = stdout.decode(
        "utf-8", errors="replace"
    )
    for line in decoded.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    return lines


def _format_python_check_strategy(
    staged_files: list[str],
    modified_files: list[str],
    untracked_files: list[str],
) -> str:
    """Render a stable check strategy for the assess agent."""
    staged = list(dict.fromkeys(staged_files))
    modified = list(dict.fromkeys(modified_files))
    untracked = list(dict.fromkeys(untracked_files))

    if staged:
        staged_preview = ", ".join(staged[:8])
        return (
            "检测到已暂存的 Python 文件。\n"
            f"- staged: {staged_preview}\n"
            "- 先运行 `make check` 与 `make type-check`，"
            "因为 Makefile 会基于 staged files 选择目标。\n"
            "- 若失败，按真实报错记录，不要归因于 allowlist。"
        )

    delta_files = list(
        dict.fromkeys(modified + untracked)
    )
    if delta_files:
        delta_preview = ", ".join(delta_files[:8])
        return (
            "未检测到 staged Python 文件，但检测到工作区中的 Python 增量文件。\n"
            f"- delta: {delta_preview}\n"
            "- 不要运行 `make check COMMITS=1` 或 "
            "`make type-check COMMITS=1`，"
            "因为这类命令可能因未选中文件而直接失败。\n"
            "- 改为对这些增量文件显式运行 "
            "`uv run ruff check <files>` 与 `uv run mypy <files>`。\n"
            "- 若文件较多，聚焦 openjiuwen/auto_harness 和 "
            "openjiuwen/harness 的相关 Python 文件。"
        )

    return (
        "当前只读快照中没有检测到 staged 或工作区 Python 增量文件。\n"
        "- 不要运行 `make check COMMITS=1` 或 "
        "`make type-check COMMITS=1`，"
        "因为 Makefile 可能因未选中文件返回 "
        "`No Python files selected`。\n"
        "- 将 lint/type-check 标记为未执行，并明确原因是"
        "“当前快照无可供 delta 检查的 Python 文件”。\n"
        "- 若时间允许，可运行 `uv run pytest tests/unit_tests -q` "
        "作为仓库健康度采样。"
    )


async def _detect_python_check_strategy(
    workspace: str,
) -> str:
    """Detect the most reliable assess-time Python check strategy."""
    staged = await _run_git_lines(
        workspace,
        "diff",
        "--name-only",
        "--cached",
        "--",
        "*.py",
    )
    changed_since_head = await _run_git_lines(
        workspace,
        "diff",
        "--name-only",
        "HEAD",
        "--",
        "*.py",
    )
    untracked = await _run_git_lines(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        "*.py",
    )
    staged_set = set(staged)
    modified = [
        path
        for path in changed_since_head
        if path not in staged_set
    ]
    return _format_python_check_strategy(
        staged, modified, untracked
    )


async def _assess_with_agent(
    config: AutoHarnessConfig,
    experience_store: Any,
) -> str:
    """调用 DeepAgent 生成报告（流式收集）。"""
    from openjiuwen.auto_harness.agents import (
        create_assess_agent,
    )

    agent = create_assess_agent(config)
    query = await _build_query(
        config, experience_store
    )

    report = ""
    async for chunk in agent.stream({"query": query}):
        text = ""
        if hasattr(chunk, "payload"):
            payload = chunk.payload
            if isinstance(payload, dict):
                text = str(
                    payload.get("content", "")
                )
        report += text

    if not report or len(report) < 100:
        logger.warning(
            "Agent report too short (%d chars), "
            "falling back",
            len(report) if report else 0,
        )
        return await _fallback_assess(
            config, experience_store
        )
    return report


async def _fallback_assess(
    config: AutoHarnessConfig,
    experience_store: Any,
) -> str:
    """回退：纯 Python 版本的评估报告。"""
    recent = await experience_store.list_recent(
        limit=10
    )
    changes = await _collect_recent_changes(
        config.workspace,
    )
    source = await _collect_source_summary(
        config.workspace,
    )

    sections = [
        "# 自动评估报告\n",
        "## 当前状态\n",
        source,
        "\n### 近期变更\n",
        changes or "_无 git 历史_",
        "\n## 近期经验\n",
        _format_experiences(recent),
        "\n## 改进方向\n",
        _derive_directions(recent),
    ]

    report = "\n".join(sections)
    logger.info(
        "Fallback assessment generated (%d chars)",
        len(report),
    )
    return report


# ----------------------------------------------------------
# internal: gap analysis
# ----------------------------------------------------------


async def _analyze_gaps_with_agent(
    config: AutoHarnessConfig,
    competitor: str,
    harness_state: str,
) -> List[Gap]:
    """调用 DeepAgent 执行差距分析（流式收集）。"""
    from openjiuwen.auto_harness.agents import (
        create_assess_agent,
    )

    agent = create_assess_agent(config)
    query = (
        f"分析 harness 与 {competitor} 的差距。\n\n"
        f"当前 harness 状态:\n"
        f"{harness_state[:3000]}\n\n"
        "输出 markdown 表格，列：\n"
        "竞品 | 功能 | 当前状态 | 差距描述 | "
        "影响(0-1) | 可行性(0-1) | "
        "建议方案 | 目标文件\n"
    )
    output = ""
    async for chunk in agent.stream({"query": query}):
        if hasattr(chunk, "payload"):
            payload = chunk.payload
            if isinstance(payload, dict):
                output += str(
                    payload.get("content", "")
                )
    return parse_gaps(output)


# ----------------------------------------------------------
# helpers
# ----------------------------------------------------------


async def _collect_recent_changes(
    workspace: str,
) -> str:
    """Run ``git log --oneline -20`` in *workspace*."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", "-20",
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()
    except (OSError, ValueError):
        logger.warning("Failed to collect git log")
        return ""


async def _collect_source_summary(
    workspace: str,
) -> str:
    """List key directories under *workspace*."""
    root = Path(workspace)
    key_dirs = [
        "openjiuwen/core",
        "openjiuwen/harness",
        "openjiuwen/auto_harness",
        "tests/unit_tests",
    ]
    lines: list[str] = []
    for d in key_dirs:
        p = root / d
        if p.is_dir():
            count = sum(1 for _ in p.rglob("*.py"))
            lines.append(
                f"- `{d}/`: {count} .py files"
            )
        else:
            lines.append(f"- `{d}/`: _not found_")
    return "\n".join(lines)


def _format_experiences(
    experiences: List[Experience],
) -> str:
    """Format experiences into markdown bullets."""
    if not experiences:
        return "_无近期经验记录_"
    lines: list[str] = []
    for exp in experiences:
        lines.append(
            f"- [{exp.type.value}] **{exp.topic}**: "
            f"{exp.summary or exp.outcome}"
        )
    return "\n".join(lines)


def _derive_directions(
    experiences: List[Experience],
) -> str:
    """Derive improvement directions from experiences."""
    if not experiences:
        return "- 收集更多运行数据后再生成改进方向"
    failures = [
        exp for exp in experiences
        if exp.type.value == "failure"
    ]
    if failures:
        topics = {exp.topic for exp in failures}
        lines = [
            f"- 修复近期失败: {t}"
            for t in sorted(topics)
        ]
        return "\n".join(lines)
    return "- 继续当前优化方向，暂无明显瓶颈"
