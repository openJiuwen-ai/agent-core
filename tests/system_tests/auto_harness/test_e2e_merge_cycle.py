# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""端到端测试 — auto-harness build_verify 后全流程（真实 LLM+已有design产物）。

流程：merge-activate → query

运行方式::
    - 修改下面EXISTING_PRODUCTS为本地runtime_extensions
"""

from __future__ import annotations
import json
import os
import unittest
from pathlib import Path
import shutil
import sys

from openjiuwen.auto_harness.orchestrator import AutoHarnessOrchestrator
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    ExtensionDesign,
    OptimizationTask,
    RuntimeExtensionArtifact,
)
from openjiuwen.auto_harness.contexts import TaskContext, TaskRuntime
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline import (
    ExtensionTaskPipeline,
    VerifiedExtensionTask,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extended_evolve_pipeline import (
    _build_merged_verified_task,
)
from openjiuwen.auto_harness.stages.merge import MergeActivationBlock, MergeSuccessResult
from openjiuwen.auto_harness.infra.runtime_extension_merger import MergedExtensionError
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig, ModelRequestConfig,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner

# ---------------------------------------------------------------------------
# 配置：本地design产物
# ---------------------------------------------------------------------------


EXISTING_PRODUCTS = [
    # 每个元素 = 一个已生成的 runtime extension 目录
    # 目录里必须有 harness_config.yaml
    Path("C:/Users/Administrator/Downloads/auto-harness/runtime_extensions/799685328dc4/finance_excel_processor"),
    Path("C:/Users/Administrator/Downloads/auto-harness/runtime_extensions/799685328dc4/huawei_ppt_generator"),
]
# 触发 query：跑完 activate 后给 agent 的指令，必须能调到新挂载的 tool/skill/rail
TRIGGER_QUERY = "使用 huawei_ppt_generator 的 huawei_ppt_generator_tool 帮我生成一个3页的ppt介绍上海"
# ---- LLM 配置：同 e2e ----
os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")
API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")


def _create_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            timeout=int(os.getenv("MODEL_TIMEOUT", "120")),
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(
            model=MODEL_NAME, temperature=0.2, top_p=0.9,
        ),
    )


class TestL4ActivateReplay(unittest.IsolatedAsyncioTestCase):
    """L4：从磁盘旧产物 → merge → activate → query 生效。"""

    async def asyncSetUp(self) -> None:
        if not API_KEY or not API_BASE:
            self.skipTest("L4 requires real LLM config")
        for p in EXISTING_PRODUCTS:
            if not (p / "harness_config.yaml").is_file():
                self.skipTest(f"Missing product: {p}")
        await Runner.start()
        # data_dir 用临时目录，避免污染原产物
        self._tmp = Path(os.environ.get(
            "L4_TMP_DIR",
            f"/tmp/ah_l4_{os.getpid()}",
        ))
        self._tmp.mkdir(parents=True, exist_ok=True)

    async def asyncTearDown(self) -> None:
        await Runner.stop()
        # 保留 tmp 目录方便事后检查；如要清理，取消下行注释
        # shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers

    def _make_config(self) -> AutoHarnessConfig:
        return AutoHarnessConfig(
            model=_create_model(),
            data_dir=str(self._tmp),
            workspace=str(self._tmp / "workspace"),
            session_budget_secs=1800.0,
            task_timeout_secs=600.0,
        )

    def _build_orch_and_agent(self, config: AutoHarnessConfig):
        from openjiuwen.auto_harness.agents import create_auto_harness_agent
        agent = create_auto_harness_agent(config)
        orch = AutoHarnessOrchestrator(config, agent=agent)
        return orch, agent

    def _stage_existing_products(self, orch: AutoHarnessOrchestrator) -> list[RuntimeExtensionArtifact]:
        """把磁盘旧产物拷贝到 orch 新 session 目录。"""
        session_root = orch.ensure_session_runtime_dir()
        artifacts: list[RuntimeExtensionArtifact] = []
        for src in EXISTING_PRODUCTS:
            dst = session_root / src.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            artifacts.append(RuntimeExtensionArtifact(
                extension_name=src.name,
                runtime_path=str(dst.resolve()),
                config_path=str((dst / "harness_config.yaml").resolve()),
            ))
        return artifacts

    def _make_verified_tasks(
            self,
            orch: AutoHarnessOrchestrator,
            artifacts: list[RuntimeExtensionArtifact],
    ) -> list[VerifiedExtensionTask]:
        """Build min-viable VerifiedExtensionTask list reusing real TaskContext."""
        verified: list[VerifiedExtensionTask] = []
        for art in artifacts:
            design = ExtensionDesign(
                gap_id=f"replay-{art.extension_name}",
                extension_name=art.extension_name,
            )
            task = OptimizationTask(
                topic=f"runtime-extension:{art.extension_name}",
            )
            ctx = TaskContext(
                orchestrator=orch,
                task=task,
                runtime=TaskRuntime(
                    related=[],
                    wt_path=str(orch.ensure_session_runtime_dir()),
                    edit_safety_rail=None,
                    preexisting_dirty_files=[],
                    task_agent=None,
                    commit_agent=None,
                ),
            )
            ctx.put_artifact("extension_target", design)
            ctx.put_artifact("runtime_extension", art)
            verified.append(VerifiedExtensionTask(
                design=design, task=task, ctx=ctx,
            ))
        return verified

    async def _drive_activate_with_auto_accept(
            self,
            orch: AutoHarnessOrchestrator,
            verified: VerifiedExtensionTask,
    ) -> dict:
        """Run run_activate_stream + auto-accept the interaction prompt."""
        captured = {
            "interaction_seen": False,
            "extension_ready": None,
            "testing_guide": "",
        }
        async for chunk in ExtensionTaskPipeline.run_activate_stream(orch, verified):
            ctype = getattr(chunk, "type", "")
            if ctype == "extension_ready":
                captured["extension_ready"] = chunk.payload
            elif ctype == "__interaction__":
                captured["interaction_seen"] = True
                async for _ in orch.run_session_stream(message={
                    "interaction_id": chunk.payload["interaction_id"],
                    "action": "accept",
                }):
                    pass
            elif ctype == "activate_testing_guide":
                captured["testing_guide"] = chunk.payload.get("text", "")
        return captured

    # ------------------------------------------------------------ tests

    async def test_multi_design_merge_activate_query(self) -> None:
        """N>1：merge → activate → query 生效。"""
        self.assertGreaterEqual(
            len(EXISTING_PRODUCTS), 2,
            "Multi-design path needs >=2 products",
        )
        config = self._make_config()
        orch, agent = self._build_orch_and_agent(config)

        # ---- Phase 1: 把旧产物挪到新 session 目录 ----
        artifacts = self._stage_existing_products(orch)
        verified_tasks = self._make_verified_tasks(orch, artifacts)

        # ---- Phase 2: merge ----
        merge_chunks: list = []
        merged: RuntimeExtensionArtifact | None = None
        try:
            async for chunk in MergeActivationBlock().stream(
                    orch, verified_tasks,
            ):
                if isinstance(chunk, MergeSuccessResult):
                    merged = chunk.artifact
                else:
                    merge_chunks.append(chunk)
        except MergedExtensionError as exc:
            self.fail(f"merge failed (检查 L1/L2 用例): {exc}")
        assert merged is not None
        self.assertTrue(Path(merged.config_path).is_file())
        self.assertTrue(Path(merged.runtime_path).is_dir())
        self.assertEqual(merged.extension_name, "merged_extensions")

        # ---- Phase 3: activate（带真 LLM 的 testing_guide + 自动 accept）----
        merged_verified = _build_merged_verified_task(orch, merged)
        result = await self._drive_activate_with_auto_accept(orch, merged_verified)
        self.assertTrue(result["interaction_seen"], "activate 必须产生 __interaction__")
        self.assertIsNotNone(result["extension_ready"])
        # 此时 agent 应已被 enqueue 了 merged config
        self.assertIn(merged.config_path, agent._pending_harness_configs)

        # ---- Phase 4: query 生效 ----
        rails_before = {type(r).__name__ for r in agent._registered_rails}
        tool_ids_before = set(_snapshot_tool_ids())
        llm_chunks: list = []
        async for chunk in Runner.run_agent_streaming(agent, {"query": TRIGGER_QUERY}):
            llm_chunks.append(chunk)
            logger.info("stream get chunk: {}".format(chunk))

        # 关键断言：drain 已发生
        self.assertEqual(
            agent._pending_harness_configs, [],
            "agent 应已 drain pending configs",
        )
        # 关键断言：新 rails/tools/skills 真的挂上去了
        rails_after = {type(r).__name__ for r in agent._registered_rails}
        tool_ids_after = set(_snapshot_tool_ids())
        new_rails = rails_after - rails_before
        new_tools = tool_ids_after - tool_ids_before
        # 关键断言：sys.modules 出现 merged 命名空间
        merged_modules = [
            m for m in sys.modules
            if m.startswith("openjiuwen.extensions.harness.merged_extensions")
               or m.startswith("openjiuwen_runtime_extensions.")
        ]
        self.assertTrue(
            merged_modules,
            "sys.modules 必须出现 merged 模块（说明 import 路径已注册）",
        )
        # 至少有 rails 或 tools 或 skill_dir 之一发生了变化
        skill_use_rail_skill_dirs = []
        for r in agent._registered_rails:
            if type(r).__name__ == "SkillUseRail":
                cur = r.skills_dir
                if isinstance(cur, list):
                    skill_use_rail_skill_dirs.extend(cur)
        new_skill_dirs = [
            sd for sd in skill_use_rail_skill_dirs
            if Path(sd).resolve().is_relative_to(Path(merged.runtime_path).resolve())
        ]
        self.assertTrue(
            new_rails or new_tools or new_skill_dirs,
            (
                "merged 扩展必须挂上至少一种组件 "
                f"(rails={new_rails}, tools={new_tools}, skills={new_skill_dirs})"
            ),
        )

        # ---- 写一份事后报告，方便排查 ----
        report = (self._tmp / "l4_report.json")
        report.write_text(json.dumps({
            "merged_runtime_path": merged.runtime_path,
            "merged_config_path": merged.config_path,
            "new_rails": sorted(new_rails),
            "new_tool_ids": sorted(map(str, new_tools)),
            "new_skill_dirs": new_skill_dirs,
            "merged_modules_count": len(merged_modules),
            "llm_chunk_types": [
                getattr(c, "type", "?") for c in llm_chunks[:50]
            ],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"L4 report: {report}")

    async def test_single_design_activate_query(self) -> None:
        """N==1：跳过 merge，直接 activate（用第一份产物即可）。"""
        config = self._make_config()
        orch, agent = self._build_orch_and_agent(config)
        artifacts = self._stage_existing_products(orch)
        verified_tasks = self._make_verified_tasks(orch, artifacts[:1])
        result = await self._drive_activate_with_auto_accept(
            orch, verified_tasks[0],
        )
        self.assertTrue(result["interaction_seen"])
        self.assertIn(artifacts[0].config_path, agent._pending_harness_configs)
        result = Runner.run_agent_streaming(agent, {"query": TRIGGER_QUERY})
        async for res in result:
            logger.info(f"Stream chunk received: {res}")
        self.assertEqual(agent._pending_harness_configs, [])


def _snapshot_tool_ids() -> list[str]:
    """Snapshot Runner.resource_mgr tool ids."""
    from openjiuwen.core.runner import Runner
    return list(getattr(Runner.resource_mgr, "_tools", {}).keys())


if __name__ == "__main__":
    unittest.main()
