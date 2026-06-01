# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Verify extension stage tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.auto_harness.contexts import (
    TaskContext,
    TaskRuntime,
)
from openjiuwen.auto_harness.orchestrator import (
    AutoHarnessOrchestrator,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    ExtensionBuildArtifact,
    OptimizationTask,
    StageResult,
)
from openjiuwen.auto_harness.stages.verify import (
    ExtendVerifyStage,
    _CIResult,
    _build_ext_acceptance_fix_prompt,
    _build_ext_acceptance_test_prompt,
    _build_ext_static_fix_prompt,
    _run_agent_generated_ext_acceptance,
    _stream_verify_ext_agent_turn,
)
from openjiuwen.auto_harness.stages.base import (
    scope_output_event_stage,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)
from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)


async def _collect_last_result(stage, ctx):
    last = None
    async for item in stage.stream(ctx):
        if isinstance(item, StageResult):
            if item.artifacts:
                ctx.put_artifacts(item.artifacts)
            last = item
    return last


async def _acceptance_success(**kwargs):
    _ = kwargs
    yield _CIResult(passed=True, errors="")


def _make_task_context(tmp_path: Path) -> TaskContext:
    wt_path = tmp_path / "wt"
    wt_path.mkdir()
    orch = AutoHarnessOrchestrator(
        AutoHarnessConfig(data_dir=str(tmp_path)),
        agent=None,
    )
    runtime = TaskRuntime(
        related=[],
        wt_path=str(wt_path),
        edit_safety_rail=None,
        preexisting_dirty_files=[],
        task_agent=None,
        commit_agent=None,
    )
    return TaskContext(
        orchestrator=orch,
        task=OptimizationTask(topic="verify ext"),
        runtime=runtime,
    )


def _write_scaffold(wt_path: Path) -> ExtensionBuildArtifact:
    """Create extension files and return build artifact."""
    ext = (
        wt_path
        / "openjiuwen"
        / "extensions"
        / "harness"
        / "demo_ext"
    )
    rails_dir = ext / "rails"
    tools_dir = ext / "tools"
    for d in (ext, rails_dir, tools_dir):
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text(
            "", encoding="utf-8"
        )

    (rails_dir / "extension_rail.py").write_text(
        "from openjiuwen.harness.rails.base "
        "import DeepAgentRail\n\n\n"
        "class ExtensionRail(DeepAgentRail):\n"
        '    """Runtime-generated rail."""\n\n'
        "    pass\n",
        encoding="utf-8",
    )

    (tools_dir / "helper.py").write_text(
        "EXTENSION_NAME = 'demo_ext'\n",
        encoding="utf-8",
    )

    (tools_dir / "extension_tool.py").write_text(
        "from __future__ import annotations\n\n"
        "from typing import Any, AsyncIterator, Dict\n\n"
        "from .helper import EXTENSION_NAME\n"
        "from openjiuwen.core.foundation.tool "
        "import Tool, ToolCard\n\n\n"
        "class ExtensionTool(Tool):\n"
        "    def __init__(self) -> None:\n"
        "        super().__init__(\n"
        "            ToolCard(\n"
        "                id='demo_ext_tool',\n"
        "                name='demo_ext_tool',\n"
        "                description=(\n"
        '                    "Runtime extension tool '
        'for "\n'
        "                    + EXTENSION_NAME\n"
        "                ),\n"
        "            )\n"
        "        )\n\n"
        "    async def invoke(\n"
        "        self,\n"
        "        inputs: Dict[str, Any],\n"
        "        **kwargs: Any,\n"
        "    ) -> Dict[str, Any]:\n"
        "        _ = kwargs\n"
        "        return {\n"
        '            "extension": EXTENSION_NAME,\n'
        '            "inputs": inputs,\n'
        "        }\n\n"
        "    async def stream(\n"
        "        self,\n"
        "        inputs: Dict[str, Any],\n"
        "        **kwargs: Any,\n"
        "    ) -> AsyncIterator[Dict[str, Any]]:\n"
        "        yield await self.invoke("
        "inputs, **kwargs)\n",
        encoding="utf-8",
    )

    manifest = ext / "harness_config.yaml"
    module_base = (
        "openjiuwen.extensions.harness.demo_ext"
    )
    manifest.write_text(
        "schema_version: harness_config.v0.1\n"
        "name: demo_ext\n"
        "resources:\n"
        "  rails:\n"
        "    - type: package\n"
        f"      module: {module_base}"
        ".rails.extension_rail\n"
        "      class: ExtensionRail\n"
        "  tools:\n"
        "    - type: package\n"
        f"      module: {module_base}"
        ".tools.extension_tool\n"
        "      class: ExtensionTool\n",
        encoding="utf-8",
    )

    return ExtensionBuildArtifact(
        extension_name="demo_ext",
        extension_root=str(ext.resolve()),
        config_path=str(manifest.resolve()),
    )


@pytest.mark.asyncio
async def test_verify_ext_loads_generated_runtime_extension(
    tmp_path: Path,
):
    ctx = _make_task_context(tmp_path)
    wt_path = tmp_path / "wt"
    build = _write_scaffold(wt_path)
    ctx.put_artifact("extension_build", build)

    with patch(
        "openjiuwen.auto_harness.stages.verify."
        "_install_extension_dependencies",
        new_callable=AsyncMock,
        return_value=(True, ""),
    ), patch(
        "openjiuwen.auto_harness.stages.verify."
        "_check_ruff",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "openjiuwen.auto_harness.stages.verify."
        "_check_imports",
        return_value=[],
    ), patch(
        "openjiuwen.auto_harness.stages.verify."
        "_run_agent_generated_ext_acceptance",
        new=_acceptance_success,
    ):
        result = await _collect_last_result(
            ExtendVerifyStage(),
            ctx,
        )

    assert result is not None
    verify_report = ctx.require_artifact("verify_report")
    assert verify_report.ci_result["passed"] is True
    assert verify_report.ci_result["rails"] == 1
    assert verify_report.ci_result["tools"] == 1


@pytest.mark.asyncio
async def test_verify_ext_fails_when_manifest_missing(
    tmp_path: Path,
):
    from openjiuwen.auto_harness.infra.runtime_extension_static_checks import (
        ExtStaticCheckResult,
    )

    ctx = _make_task_context(tmp_path)
    wt_path = tmp_path / "wt"
    build = _write_scaffold(wt_path)
    Path(build.config_path).unlink()
    ctx.put_artifact("extension_build", build)

    static_result = ExtStaticCheckResult(
        errors=["manifest_missing: harness_config.yaml"],
        rails_count=0,
        tools_count=0,
        skills_count=0,
        skill_dirs_count=0,
    )

    with patch(
        "openjiuwen.auto_harness.stages.verify."
        "_install_extension_dependencies",
        new_callable=AsyncMock,
        return_value=(True, ""),
    ), patch(
        "openjiuwen.auto_harness.stages.verify."
        "run_static_checks_against_runtime",
        new_callable=AsyncMock,
        return_value=static_result,
    ):
        result = await _collect_last_result(
            ExtendVerifyStage(),
            ctx,
        )

    assert result is not None
    assert result.status == "failed"
    task_result = ctx.require_artifact("task_result")
    assert task_result.success is False


@pytest.mark.asyncio
async def test_verify_ext_repairs_manifest_schema_failure(
    tmp_path: Path,
):
    ctx = _make_task_context(tmp_path)
    wt_path = tmp_path / "wt"
    build = _write_scaffold(wt_path)
    manifest = Path(build.config_path)
    manifest.write_text(
        "schema_version: harness_config.v0.1\n"
        "name: demo_ext\n"
        "description:\n"
        "  cn: 演示扩展\n"
        "  en: demo extension\n"
        "resources:\n"
        "  tools:\n"
        "    - type: package\n"
        "      module: openjiuwen.extensions.harness.demo_ext.tools.extension_tool\n"
        "      class: ExtensionTool\n",
        encoding="utf-8",
    )
    ctx.put_artifact("extension_build", build)
    prompts: list[str] = []

    class FakeAgent:
        card = AgentCard(
            id="fake-agent",
            name="fake-agent",
        )

        async def stream(self, inputs, session=None):
            del session
            query = inputs["query"]
            prompts.append(query)
            assert "description` 必须是字符串" in query
            manifest.write_text(
                "schema_version: harness_config.v0.1\n"
                "name: demo_ext\n"
                "description: 演示扩展\n"
                "resources:\n"
                "  tools:\n"
                "    - type: package\n"
                "      module: openjiuwen.extensions.harness.demo_ext.tools.extension_tool\n"
                "      class: ExtensionTool\n",
                encoding="utf-8",
            )
            yield OutputSchema(
                type="message",
                index=0,
                payload={"content": "fixed"},
            )

    ctx.runtime.task_agent = FakeAgent()

    with patch(
        "openjiuwen.auto_harness.stages.verify."
        "_install_extension_dependencies",
        new_callable=AsyncMock,
        return_value=(True, ""),
    ), patch(
        "openjiuwen.auto_harness.stages.verify."
        "_check_ruff",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "openjiuwen.auto_harness.stages.verify."
        "_run_agent_generated_ext_acceptance",
        new=_acceptance_success,
    ):
        result = await _collect_last_result(
            ExtendVerifyStage(),
            ctx,
        )

    assert result is not None
    assert result.status != "failed"
    assert len(prompts) == 1
    verify_report = ctx.require_artifact("verify_report")
    assert verify_report.ci_result["passed"] is True


def test_verify_ext_prompt_requires_artifact_level_acceptance(
    tmp_path: Path,
):
    build = ExtensionBuildArtifact(
        extension_name="huawei_ppt_generator",
        extension_root=str(tmp_path / "huawei_ppt_generator"),
        config_path=str(
            tmp_path
            / "huawei_ppt_generator"
            / "harness_config.yaml"
        ),
    )

    prompt = _build_ext_acceptance_test_prompt(
        build=build,
        test_file=tmp_path / "test_huawei_ppt_generator.py",
        python_executable="/usr/bin/python3",
        rails_count=0,
        tools_count=1,
        skills_count=1,
        previous_error="",
    )

    assert "文件产物验收 (仅文件生成类 Tool)" in prompt
    assert "tmp_path" in prompt
    assert "zipfile" in prompt
    assert "ppt/presentation.xml" in prompt
    assert "slide*.xml" in prompt
    assert "禁止 JSON/Markdown 冒充" in prompt
    assert "必须从 harness_config.yaml 实际声明的 module/class 获取" in prompt
    assert "openjiuwen.extensions.harness.<extension_name>" in prompt


def test_verify_ext_fix_prompt_rejects_placeholder_artifacts(
    tmp_path: Path,
):
    build = ExtensionBuildArtifact(
        extension_name="huawei_ppt_generator",
        extension_root=str(tmp_path / "huawei_ppt_generator"),
        config_path=str(
            tmp_path
            / "huawei_ppt_generator"
            / "harness_config.yaml"
        ),
    )

    prompt = _build_ext_acceptance_fix_prompt(
        build=build,
        test_file=tmp_path / "test_huawei_ppt_generator.py",
        pytest_output="artifact_placeholder_output",
        python_executable="/usr/bin/python3",
    )

    assert "禁止返回 JSON/Markdown 占位" in prompt
    assert "禁止 JSON/Markdown 冒充" in prompt
    assert "success=true" in prompt


def test_verify_ext_static_fix_prompt_uses_manifest_modules(
    tmp_path: Path,
):
    build = ExtensionBuildArtifact(
        extension_name="huawei_ppt_generator",
        extension_root=str(tmp_path / "huawei_ppt_generator"),
        config_path=str(
            tmp_path
            / "huawei_ppt_generator"
            / "harness_config.yaml"
        ),
    )

    prompt = _build_ext_static_fix_prompt(
        build=build,
        static_errors="module import failed",
    )

    assert "harness_config.yaml 中实际声明的 module/class" in prompt
    assert "不要手写或猜测路径" in prompt
    assert "openjiuwen.extensions.harness.<extension_name>." in prompt


@pytest.mark.asyncio
async def test_verify_ext_reuses_generated_test_after_fix(
    tmp_path: Path,
):
    ctx = _make_task_context(tmp_path)
    wt_path = tmp_path / "wt"
    build = _write_scaffold(wt_path)
    prompt_kinds: list[str] = []

    class FakeAgent:
        card = AgentCard(
            id="fake-agent",
            name="fake-agent",
        )

        async def stream(self, inputs, session=None):
            del session
            query = inputs["query"]
            if "测试文件必须写入: " in query:
                prompt_kinds.append("generate")
                raw_path = query.split(
                    "测试文件必须写入: ", 1
                )[1].splitlines()[0]
                test_file = Path(raw_path.strip())
                test_file.parent.mkdir(
                    parents=True, exist_ok=True
                )
                test_file.write_text(
                    "# frozen acceptance test\n"
                    "def test_runtime_extension_acceptance():\n"
                    "    assert True\n",
                    encoding="utf-8",
                )
            else:
                prompt_kinds.append("fix")
                assert "verify_ext 验收测试失败" in query
            yield OutputSchema(
                type="message",
                index=0,
                payload={"content": "ok"},
            )

    ctx.runtime.task_agent = FakeAgent()
    run_results = [
        _CIResult(passed=False, errors="first failure"),
        _CIResult(passed=True, errors=""),
    ]
    seen_test_contents: list[str] = []

    async def _fake_run_pytest_file(
        *,
        python_executable,
        test_file,
        cwd,
    ):
        del python_executable, cwd
        seen_test_contents.append(
            Path(test_file).read_text(encoding="utf-8")
        )
        return run_results.pop(0)

    with patch(
        "openjiuwen.auto_harness.stages.verify."
        "_run_pytest_file",
        new=_fake_run_pytest_file,
    ):
        items = [
            item
            async for item in _run_agent_generated_ext_acceptance(
                ctx=ctx,
                build=build,
                rails_count=1,
                tools_count=1,
                skills_count=0,
            )
        ]

    assert prompt_kinds == ["generate", "fix"]
    assert len(seen_test_contents) == 2
    assert seen_test_contents[0] == seen_test_contents[1]
    assert any(
        isinstance(item, _CIResult) and item.passed
        for item in items
    )


@pytest.mark.asyncio
async def test_verify_ext_agent_turn_uses_fresh_open_session():
    seen_sessions = []

    class FakeAgent:
        card = AgentCard(
            id="fake-agent",
            name="fake-agent",
        )

        async def stream(self, inputs, session=None):
            assert inputs["query"] == "write tests"
            assert session is not None
            emitter = (
                session._inner.stream_writer_manager()
                .stream_emitter()
            )
            assert not emitter.is_closed()
            seen_sessions.append(session.get_session_id())
            yield "chunk"

    chunks = [
        item
        async for item in _stream_verify_ext_agent_turn(
            FakeAgent(),
            "write tests",
            session_id_prefix="verify-ext-test",
        )
    ]

    assert chunks == ["chunk"]
    assert len(seen_sessions) == 1
    assert seen_sessions[0].startswith("verify-ext-test-")


def test_verify_ext_scopes_nested_agent_stage_events():
    chunk = OutputSchema(
        type="stage_result",
        index=0,
        payload={
            "stage": "verify",
            "status": "success",
        },
    )

    scoped = scope_output_event_stage(
        chunk,
        "verify_ext",
    )

    assert scoped.payload["stage"] == "verify_ext"
    assert chunk.payload["stage"] == "verify"
