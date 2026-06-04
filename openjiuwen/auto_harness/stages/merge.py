# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Merge stage: combine multiple verified runtime extensions into one."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.auto_harness.infra.runtime_extension_merger import (
    MergedExtensionError,
    MergeRuntimeExtensionsResult,
    merge_runtime_extensions,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline import (
    VerifiedExtensionTask,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    ExtensionDesign,
    RuntimeExtensionArtifact,
)
from openjiuwen.auto_harness.infra.runtime_extension_static_checks import (
    run_static_checks_against_runtime,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)
from openjiuwen.core.common.logging import logger


if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


@dataclass(frozen=True)
class MergeSuccessResult:
    """Structured result yielded on merge success; distinct from status events."""

    artifact: RuntimeExtensionArtifact


class MergeActivationBlock:
    """Merge multiple verified extensions and run static checks.

    On first static-check failure, creates a merge_agent and retries up to
    3 rounds.  On success, yields a ``MergeSuccessResult`` containing the
    merged artifact followed by a ``"success"`` status event.
    On exhaustion, raises ``MergedExtensionError`` (fail-fast).
    """

    name = "merge_ext"

    async def stream(
        self,
        orchestrator: Any,
        verified_tasks: list[VerifiedExtensionTask],
        package_name: str = "",
    ) -> AsyncIterator[Any]:
        from openjiuwen.auto_harness.agents.factory import (
            create_merge_ext_agent,
        )

        session_root = Path(orchestrator.ensure_session_runtime_dir())
        runtime_exts = [t.ctx.require_artifact("runtime_extension") for t in verified_tasks]
        designs = [t.design for t in verified_tasks]

        # Use pre-generated package_name if available
        if package_name:
            merged_name = package_name
            logger.info(
                "[MergeActivate] using pre-generated package_name: %s",
                merged_name
            )
        else:
            # Fallback: derive from designs (legacy behavior)
            merged_name = await _derive_merged_name(
                orchestrator.config,
                designs,
            )

        yield _merge_event("running", merged_name=merged_name, repair_rounds=0)

        try:
            merge_result = merge_runtime_extensions(runtime_exts, session_root, merged_name)
        except MergedExtensionError as exc:
            yield _merge_event("failed", merged_name=merged_name, error=str(exc))
            raise

        max_attempts = 3
        merged = merge_result.runtime_ext

        result = await run_static_checks_against_runtime(
            runtime_ext=merged,
            session_id_prefix=f"merge_{orchestrator.runtime.session_id}",
        )

        agent: DeepAgent | None = None
        for attempt in range(1, max_attempts + 1):
            if not result.errors:
                logger.info("[MergeActivate] merge static check no errors")
                break
            if agent is None:
                agent = create_merge_ext_agent(
                    orchestrator.config,
                    workspace_override=merged.runtime_path,
                    extra_rails=(orchestrator.stream_rails or None),
                )
            prompt = _build_merge_fix_prompt(
                merged=merged,
                merge_result=merge_result,
                static_errors=result.errors,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            async for chunk in _stream_merge_agent_turn(
                agent,
                prompt,
                session_id_prefix=f"merge-{merged.extension_name}-fix-{attempt}",
            ):
                yield chunk

            result = await run_static_checks_against_runtime(
                runtime_ext=merged,
                session_id_prefix=f"merge_{orchestrator.runtime.session_id}_{uuid.uuid4().hex[:8]}",
            )

        if result.errors:
            yield _merge_event(
                "failed",
                merged_name=merged_name,
                error="; ".join(result.errors),
                repair_rounds=max_attempts,
            )
            raise MergedExtensionError(f"merged extension static checks failed after {max_attempts} repair rounds")

        # Merge 成功，清理源扩展目录（文件已全部复制到合并后的扩展）
        for art in runtime_exts:
            src_path = Path(art.runtime_path)
            if src_path.exists():
                logger.info("[MergeActivate] cleanup source extension dir: %s", src_path)
                shutil.rmtree(src_path, ignore_errors=True)

        logger.info(
            "[MergeActivate] merge success, name={}, tools_count: {}, rails_count: {}, skills_count: {}".
            format(merged_name, result.tools_count, result.rails_count, result.skills_count)
        )
        yield MergeSuccessResult(artifact=merged)
        yield _merge_event("success", merged_name=merged_name)


async def _derive_merged_name(
    config: AutoHarnessConfig,
    designs: list[ExtensionDesign],
) -> str:
    """调用模型生成语义化的合并名称，失败时使用 fallback。"""
    if not designs:
        return "merged_extensions"
    if len(designs) == 1:
        return designs[0].extension_name  # 单扩展保持原名，不需要模型

    # 构建命名提示
    ext_names = [d.extension_name for d in designs]
    prompt = (
        "根据以下 runtime extension 名称列表，生成一个语义化的合并扩展名称。\n"
        "规则：\n"
        "1. snake_case 格式\n"
        "2. 理解共同能力，表达主要功能组合\n"
        "3. 不超过 40 字符\n"
        f"扩展列表: {json.dumps(ext_names, ensure_ascii=False)}\n\n"
        "只输出名称，不要解释。示例输出格式：office_ppt_generator\n"
    )

    # 调用轻量 agent（极简配置，避免复杂 rails 初始化开销）
    try:
        output = await _call_naming_model(config, prompt)
        name = _parse_name_output(output)
        if name:
            logger.info(
                "[MergeActivate] agent generated merged name: %s from designs: %s",
                name, ext_names
            )
            return name
    except Exception:
        logger.warning("[MergeActivate] naming agent failed, using fallback")

    # Fallback: 规则生成
    return _derive_merged_name_fallback(designs)


async def _call_naming_model(
    config: AutoHarnessConfig,
    prompt: str
) -> str:
    """直接调用模型生成名称。
    """
    if config.model is None:
        raise RuntimeError("config.model is None, cannot call naming agent")

    # 直接调用模型，无需 Agent/session/rails
    response = await config.model.invoke(
        messages=prompt,
        max_tokens=100,  # 名称很短，100 tokens 足够
        timeout=config.model_timeout_secs,
    )

    # response 是 AssistantMessage，提取 content
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()
    # 处理可能的 list[dict] 格式
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
            elif isinstance(item, str):
                texts.append(item)
        return "".join(texts).strip()
    return str(content).strip()


def _parse_name_output(output: str) -> str | None:
    """解析模型输出，提取 snake_case 名称。"""
    if not output:
        return None
    # 提取第一个 snake_case 名称（只包含字母、数字、下划线）
    output = output.strip()
    # 尝试匹配纯 snake_case
    match = re.match(r"^[a-z][a-z0-9_]{0,39}$", output)
    if match:
        return match.group(0)
    # 尝试从文本中提取
    matches = re.findall(r"[a-z][a-z0-9_]{2,39}", output)
    if matches:
        # 优先选择最长的合理名称
        for m in sorted(matches, key=len, reverse=True):
            if len(m) >= 3:
                return m
    return None


def _derive_merged_name_fallback(designs: list[ExtensionDesign]) -> str:
    """根据设计列表生成合并后的扩展名称（规则 fallback）。"""
    if not designs:
        return "merged_extensions"
    if len(designs) == 1:
        return designs[0].extension_name  # 单扩展保持原名

    # 多扩展：以第一个 capability 或 constraint 为主，添加 _merged 后缀
    constraints = [d for d in designs if getattr(d, "kind", "") == "constraint"]
    capabilities = [d for d in designs if getattr(d, "kind", "") != "constraint"]
    primary = (capabilities + constraints)[0]
    return f"{primary.extension_name}_merged"


def _merge_event(
    status: str,
    *,
    merged_name: str = "merged_extensions",
    repair_rounds: int = 0,
    error: str = "",
) -> OutputSchema:
    """Produce an OutputSchema for the merge stage."""
    return OutputSchema(
        type="stage_result",
        index=0,
        payload={
            "stage": "activate",
            "parent_stage": "activate",
            "extension_stage": "merge_ext",
            "extension_name": merged_name,
            "status": status,
            "repair_rounds": repair_rounds,
            "error": error,
            "messages": [],
            "metrics": {},
        },
    )


def _build_merge_fix_prompt(
    *,
    merged: RuntimeExtensionArtifact,
    merge_result: MergeRuntimeExtensionsResult,
    static_errors: list[str],
    attempt: int,
    max_attempts: int,
) -> str:
    """Build the repair prompt for the merge agent."""
    rename_summary = _format_map_summary(merge_result.rename_map)
    skill_rename_summary = _format_map_summary(merge_result.skill_rename_map)
    source_summary = json.dumps(
        merge_result.source_exts_summary,
        ensure_ascii=False,
        indent=2,
    )
    errors_text = "\n".join(static_errors)[:6000]
    merged_name = merged.extension_name
    merged_prefix = f"openjiuwen.extensions.harness.{merged_name}"

    return (
        "合并产物的静态校验失败（manifest schema / "
        "组件加载 / ruff）。\n"
        f"请只修改 {merged_name}/ 内文件，把校验跑过去。\n\n"
        f"{merged_name} 根目录: {merged.runtime_path}\n"
        f"harness_config: {merged.config_path}\n"
        f"来源扩展: {source_summary}\n\n"
        "合并器已经做过的事：\n"
        f"1. 把每个源扩展的文件扁平复制进 {merged_name}/\n"
        "2. 同相对路径冲突的文件按 <stem>__<src_ext>.<suffix> 改名"
        "（其它文件保留原名）\n"
        f"3. 改写绝对/相对 import 中的 src_ext 前缀为 {merged_name}\n"
        "4. 改写 harness_config.yaml 中所有 module 字段为 "
        f"{merged_prefix}.*\n"
        "5. skills/ 仅在 skill 名冲突时按 <skill_name>__<src_ext> 改名，"
        "不同名 skill 保持原名\n"
        "6. 所有包目录 __init__.py 已重写为空文件，"
        "不复制源扩展里的 __init__.py 内容\n\n"
        f"本次合并的具体改名摘要（只列非 identity 条目）：\n"
        f"- 文件 rename_map: {rename_summary}\n"
        f"- Skill skill_rename_map: {skill_rename_summary}\n\n"
        "这些合并器动作是可信前提，不要反向撤销。"
        "静态失败的高概率原因通常是：\n"
        "- 动态 import / importlib 字符串没有被 AST rewrite 覆盖\n"
        "- __file__ / Path 派生路径仍假设原 extension 根目录\n"
        "- skill frontmatter 或配置文本里残留旧 module/path\n"
        "- 相对 import 形态特殊，合并器没有识别到\n"
        "- manifest module 指向的对象存在，但构造函数依赖旧路径或旧包名\n\n"
        "修复硬约束（破坏即失败）：\n"
        f"- 只能修改 {merged_name}/ 内文件；"
        "不能改源扩展、harness 主代码、auto_harness 主代码\n"
        "- harness_config.yaml 中所有 module 必须仍以 "
        f"{merged_prefix} 开头\n"
        "- extension_name 不要碰\n"
        "- 不要给已被 rename 的文件再改名；"
        "不要给未冲突文件加 __<src_ext> 后缀\n"
        "- 优先修复：动态 import / __file__ 派生路径 / "
        "skill frontmatter 旧路径 / 漏改的相对 import\n"
        "- 不要修业务逻辑\n\n"
        f"修复轮次: {attempt}/{max_attempts}\n\n"
        f"失败信息:\n{errors_text}"
    )


def _format_map_summary(
    mapping: dict[tuple[str, str], str],
) -> str:
    if not mapping:
        return "none"
    lines = []
    for (src, old), new in sorted(mapping.items()):
        lines.append(f"  ({src}, {old}) -> {new}")
    return "\n".join(lines)


async def _stream_merge_agent_turn(
    agent: "DeepAgent",
    prompt: str,
    *,
    session_id_prefix: str,
) -> AsyncIterator[Any]:
    """Stream one merge agent turn with a fresh stream session.

    Mirrors _stream_verify_ext_agent_turn from verify.py.
    """
    from openjiuwen.core.session.agent import (
        create_agent_session,
    )

    session = create_agent_session(
        session_id=(
            f"{session_id_prefix}-{uuid.uuid4().hex[:8]}"
        ),
        card=getattr(agent, "card", None),
        close_stream_on_post_run=False,
    )
    await session.pre_run(inputs={"query": prompt})
    try:
        async for chunk in agent.stream(
            {"query": prompt},
            session=session,
        ):
            yield chunk
    finally:
        await session.post_run()
