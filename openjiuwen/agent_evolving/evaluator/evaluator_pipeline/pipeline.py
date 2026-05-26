import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger

from .benchmark_adapter.config import PipelineConfig, IterationResult, PipelineResult
from .benchmark_adapter import ContainerManager, SkillEvolutionManager, extract_specific_errors, Verifier
from .agent_adapter import JiuWenSwarmAdapter


class SkillEvolutionPipeline:
    """Main pipeline orchestrator with base image support"""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.container_manager = ContainerManager(config)
        self.skill_manager = SkillEvolutionManager(config)
        self.agent_adapter = JiuWenSwarmAdapter(config)
        self.agent_adapter.set_resolved_skill_name(self.skill_manager.resolved_skill_name)
        self.agent_adapter.set_all_skill_names(self.skill_manager.get_all_skill_names())
        self.verifier = Verifier(config)

        self.results: list[IterationResult] = []
        self.output_dir = config.results_dir / config.task_id
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(cls, config_path: str | Path) -> "SkillEvolutionPipeline":
        config = PipelineConfig.from_yaml(Path(config_path))
        return cls(config)

    async def run(self) -> PipelineResult:
        logger.info("\n%s", "=" * 60)
        logger.info("Evaluator Pipeline: %s", self.config.name)
        logger.info("Task: %s", self.config.task_id)
        logger.info("Max Iterations: %d", self.config.max_iterations)
        logger.info("Base Image: %s", self.config.base_image)
        logger.info("Auto-rewrite Dockerfile: %s", self.config.auto_rewrite_dockerfile)
        logger.info("%s\n", "=" * 60)

        if not self.config.api_key:
            logger.warning("WARNING: No API key configured!")
            logger.warning("  Please set one of the following environment variables:")
            logger.warning("    - DASHSCOPE_API_KEY (for DashScope API)")
            logger.warning("    - OPENAI_API_KEY (for OpenAI API)")
            logger.warning("  Or set 'api_key' in a YAML configuration file.")
            logger.warning("  Without an API key, JiuWenSwarm will not work properly.\n")
        else:
            logger.info("✓ API key configured: %s...", self.config.api_key[:10])
            logger.info("  API base: %s", self.config.api_base or "default")
            logger.info("  Model: %s\n", self.config.model_name)

        await self._init_stage()

        previous_result = None
        for iteration in range(1, self.config.max_iterations + 1):
            logger.info("\n%s", "=" * 60)
            logger.info("Iteration %d/%d", iteration, self.config.max_iterations)
            logger.info("%s\n", "=" * 60)

            iteration_result, raw_output, stderr = await self._run_iteration(
                iteration,
                previous_result=previous_result
            )
            self.results.append(iteration_result)

            await self._save_iteration_result(iteration_result, iteration, raw_output, stderr)

            previous_result = iteration_result

            if self._check_convergence():
                if all(r.test_passed for r in self.results[-self.config.convergence_threshold:]):
                    logger.info("\n✓ Convergence achieved at iteration %d (all tests passing)", iteration)
                else:
                    stagnation_window = self.results[-self.config.stagnation_patience:]
                    logger.warning(
                        "\nDeadlock stagnation at iteration %d: "
                        "pass rate stuck at %.0f%% "
                        "with no skill changes for %d iterations",
                        iteration,
                        stagnation_window[0].test_pass_rate * 100,
                        self.config.stagnation_patience
                    )
                break

        metrics = self._calculate_metrics()
        report_path = await self._generate_report(metrics)

        return PipelineResult(
            task_id=self.config.task_id,
            agent=self.config.agent,
            total_iterations=len(self.results),
            convergence_achieved=self._check_convergence(),
            results=self.results,
            metrics=metrics,
            skill_history=self.skill_manager.skill_history,
            output_dir=self.output_dir,
            report_path=report_path
        )

    async def _init_stage(self) -> None:
        logger.info("Stage 1: Initialization")

        logger.info("  - Ensuring base image exists...")
        base_ok = await self.container_manager.ensure_base_image()
        if not base_ok:
            raise RuntimeError("Failed to ensure base image exists")

        logger.info("  - Building Docker image (with auto-rewrite if needed)...")
        success = await self.container_manager.build_image()
        if not success:
            raise RuntimeError("Failed to build Docker image")

        logger.info("  - Creating output directories...")
        (self.output_dir / "iterations").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "skill_history").mkdir(parents=True, exist_ok=True)

        logger.info("  - Loading initial skills (if exist)...")
        initial_skills = self.skill_manager.load_all_skills()
        if initial_skills:
            logger.info("    Found %d existing skill(s): %s", len(initial_skills), list(initial_skills.keys()))
            for sn, sc in initial_skills.items():
                logger.info("      - %s: %d chars", sn, len(sc))
        else:
            logger.info("    No existing skills found, will create from scratch")

        logger.info("✓ Initialization complete\n")

    async def _run_iteration(
        self,
        iteration: int,
        previous_result: IterationResult | None = None
    ) -> tuple[IterationResult, str, str]:
        started_at = datetime.now(timezone.utc)

        skip_evolution = (
            previous_result is not None
            and previous_result.test_pass_rate >= 1.0
        )
        if skip_evolution:
            logger.info("  ✓ Previous iteration passed 100%%, skipping evolution")

        logger.info("  1. Starting container...")
        container_id = await self.container_manager.start_container()
        if not container_id:
            raise RuntimeError(f"Failed to start container for iteration {iteration}")

        try:
            logger.info("  2. Setting up JiuWenSwarm (verifying from base image)...")
            setup_ok = await self.agent_adapter.setup(self.container_manager)
            if not setup_ok:
                logger.warning("  JiuWenSwarm setup verification failed")

            logger.info("  3. Copying task files to container...")
            await self._copy_task_files_to_container()

            logger.info("  4. Loading skills to container...")
            all_skills = self.skill_manager.load_all_skills()
            all_evolutions = self.skill_manager.all_evolutions
            all_evolution_files = self.skill_manager.all_evolution_files
            has_skill = len(all_skills) > 0

            if has_skill:
                loaded_count = await self.agent_adapter.load_all_skills(
                    self.container_manager, all_skills,
                    all_evolutions=all_evolutions,
                    all_evolution_files=all_evolution_files,
                )
                logger.info("    %d skills loaded", loaded_count)
                for sn, sc in all_skills.items():
                    logger.info("      - %s: %d chars", sn, len(sc))
                    if sn in all_evolutions:
                        logger.info("        evolutions: %d chars", len(all_evolutions[sn]))
                    if sn in all_evolution_files:
                        logger.info("        evolution files: %s", list(all_evolution_files[sn].keys()))
            else:
                logger.info("    No existing skills found")

            self.agent_adapter.set_all_skill_names(self.skill_manager.get_all_skill_names())

            evolution_suggestions = None
            if previous_result and has_skill and not skip_evolution:
                evolution_suggestions = self._generate_evolution_suggestions(previous_result)
                if evolution_suggestions:
                    logger.info("  4.5. Evolution suggestions from previous iteration:")
                    logger.info("    %s...", evolution_suggestions[:200])

            logger.info("  5. Running JiuWenSwarm agent...")
            instruction = self._load_instruction()

            from .agent_adapter.adapter import RunContext
            run_context = RunContext(
                container=self.container_manager,
                instruction=instruction,
                iteration=iteration,
                has_skill=has_skill,
                evolution_suggestions=evolution_suggestions,
                previous_result=previous_result if not skip_evolution else None,
                evolution_files=None
            )
            agent_result = await self.agent_adapter.run(run_context)

            logger.info("  6. Running tests...")
            test_result = await self.verifier.run_tests(self.container_manager)
            logger.info("    Test passed: %s", test_result.get("passed", False))
            logger.info("    Test pass rate: %.2f%%", test_result.get("pass_rate", 0) * 100)
            if not test_result.get("passed", False):
                logger.info("    Test output (last 200 chars): %s", test_result.get("output", "")[-200:])

            logger.info("  7. Capturing created/updated skills...")
            created_skills, evolution_contents, captured_evolution_files = \
                await self.agent_adapter.capture_created_skills(self.container_manager)

            skill_changed = False
            evolution_suggestions = None

            logger.info("    Created skills: %s", list(created_skills.keys()))
            logger.info("    Previously known skills: %s", self.skill_manager.get_all_skill_names())

            if created_skills:
                resolved_name = self.skill_manager.resolved_skill_name
                if resolved_name not in created_skills and self.config.task_id not in created_skills:
                    available = list(created_skills.keys())
                    new_names = [n for n in available if n not in self.skill_manager.all_skills]
                    if new_names:
                        self.skill_manager.resolved_skill_name = new_names[0]
                        self.agent_adapter.set_resolved_skill_name(new_names[0])
                        self.skill_manager.save_resolved_skill_name()
                        logger.info("    Resolved skill name set to first new skill: '%s'", new_names[0])

                for sn in created_skills:
                    if sn not in self.skill_manager.all_skills:
                        logger.info("    ✓ New skill discovered: %s", sn)

                self.agent_adapter.set_all_skill_names(list(created_skills.keys()))

                any_skill_changed = False
                for sn, sc in created_skills.items():
                    old_content = self.skill_manager.all_skills.get(sn)
                    if (old_content is None or 
                        self.skill_manager.compute_skill_hash(old_content) != 
                        self.skill_manager.compute_skill_hash(sc)):
                        any_skill_changed = True
                        break

                has_new_evolutions = any(
                    sn in evolution_contents and evolution_contents[sn]
                    for sn in created_skills
                )
                has_new_evolution_files = any(
                    sn in captured_evolution_files and captured_evolution_files[sn]
                    for sn in created_skills
                )

                if any_skill_changed or has_new_evolutions or has_new_evolution_files:
                    self.skill_manager.save_all_skills(
                        created_skills, iteration,
                        evolutions=evolution_contents,
                        evolution_files=captured_evolution_files,
                    )
                    logger.info("    ✓ All %d skills saved to iteration_%03d", len(created_skills), iteration)
                    for sn in created_skills:
                        if (self.skill_manager.skill_dir / sn / "SKILL.md").exists():
                            await self.skill_manager.render_evolution_to_skill_md_for(sn)
                else:
                    logger.warning("    No skill content changed from previous version")
                skill_changed = any_skill_changed or has_new_evolutions or has_new_evolution_files
            else:
                if iteration == 1:
                    logger.warning("    No skill created in first iteration")
                    logger.warning("    Agent should create skill at: %s/<skill-name>/SKILL.md", 
                                    self.agent_adapter.JIUWENSWARM_SKILL_DIR)
                else:
                    logger.warning("    No skill captured in iteration %d", iteration)

            completed_at = datetime.now(timezone.utc)

            main_skill_content = self.skill_manager.current_skill

            return IterationResult(
                iteration=iteration,
                skill_content=main_skill_content,
                skill_hash=self.skill_manager.compute_skill_hash(main_skill_content),
                agent_output=agent_result.get("final_response") or "",
                agent_trajectory=agent_result["trajectory"],
                agent_execution_time=agent_result["execution_time"],
                agent_tokens_used=agent_result["tokens_used"],
                test_passed=test_result["passed"],
                test_pass_rate=test_result["pass_rate"],
                test_details=test_result,
                skill_changed=skill_changed,
                evolution_suggestions=evolution_suggestions,
                started_at=started_at,
                completed_at=completed_at,
                evolution_events=agent_result.get("evolution_events", [])
            ), agent_result.get("raw_output", ""), agent_result.get("stderr", "")

        finally:
            logger.info("  7. Saving JiuWenSwarm logs (evolution wait already done in runner)...")
            await self._save_jiuwenswarm_logs(iteration)
            logger.info("  9. Stopping container...")
            await self.container_manager.stop_container()

    async def _save_jiuwenswarm_logs(self, iteration: int) -> None:
        try:
            log_dir = self.output_dir / f"iterations/iteration_{iteration:03d}/logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            agent_logs_dir = log_dir / "agent_logs"
            agent_logs_dir.mkdir(parents=True, exist_ok=True)

            list_result = await self.container_manager.exec_in_container(
                "find ~/.jiuwenswarm/agent/.logs -type f 2>/dev/null",
                timeout=10
            )

            saved_count = 0
            if list_result.get("returncode") == 0 and list_result.get("stdout"):
                log_files_list = list_result["stdout"].strip().split("\n")
                for log_path in log_files_list:
                    if log_path:
                        log_name = log_path.split("/")[-1]
                        try:
                            result = await self.container_manager.exec_in_container(
                                f"cat {log_path} 2>/dev/null | tail -1000",
                                timeout=10
                            )

                            if result.get("returncode") == 0 and result.get("stdout"):
                                log_content = result["stdout"]
                                log_file = agent_logs_dir / log_name
                                log_file.write_text(log_content, encoding="utf-8")
                                saved_count += 1
                                logger.info("    ✓ Saved agent_logs/%s (%d chars)", log_name, len(log_content))
                        except Exception as e:
                            logger.warning("    Failed to save agent_logs/%s: %s", log_name, e)

            logs_dir = log_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)

            list_result = await self.container_manager.exec_in_container(
                "find ~/.jiuwenswarm/logs/logs -type f 2>/dev/null",
                timeout=10
            )

            if list_result.get("returncode") == 0 and list_result.get("stdout"):
                log_files_list = list_result["stdout"].strip().split("\n")
                for log_path in log_files_list:
                    if log_path:
                        log_name = log_path.split("/")[-1]
                        try:
                            result = await self.container_manager.exec_in_container(
                                f"cat {log_path} 2>/dev/null | tail -1000",
                                timeout=10
                            )

                            if result.get("returncode") == 0 and result.get("stdout"):
                                log_content = result["stdout"]
                                log_file = logs_dir / log_name
                                log_file.write_text(log_content, encoding="utf-8")
                                saved_count += 1
                                logger.info("    ✓ Saved logs/%s (%d chars)", log_name, len(log_content))
                        except Exception as e:
                            logger.warning("    Failed to save logs/%s: %s", log_name, e)

            config_files = [
                (".env", "~/.jiuwenswarm/config/.env"),
                ("config.yaml", "~/.jiuwenswarm/config/config.yaml"),
            ]

            for config_name, container_path in config_files:
                try:
                    result = await self.container_manager.exec_in_container(
                        f"cat {container_path} 2>/dev/null",
                        timeout=10
                    )

                    if result.get("returncode") == 0 and result.get("stdout"):
                        config_content = result["stdout"]
                        config_file = log_dir / config_name
                        config_file.write_text(config_content, encoding="utf-8")
                        saved_count += 1
                        logger.info("    ✓ Saved %s (%d chars)", config_name, len(config_content))
                except Exception as e:
                    logger.warning("    Failed to save %s: %s", config_name, e)

            llm_log_saved = False
            workspace = self.config.workspace_dir
            llm_search_paths = [
                "./logs/llm.log",
                f"{workspace}/logs/llm.log",
                "/root/logs/llm.log",
                "/app/logs/llm.log",
                "/workspace/logs/llm.log",
                "/home/logs/llm.log",
                "~/.jiuwenswarm/logs/logs/llm.log",
                "~/.jiuwenswarm/agent/.logs/llm.log",
                "~/.jiuwenswarm/llm.log",
            ]
            for llm_path in llm_search_paths:
                try:
                    result = await self.container_manager.exec_in_container(
                        f"cat {llm_path} 2>/dev/null | tail -2000",
                        timeout=10
                    )
                    if result.get("returncode") == 0 and result.get("stdout"):
                        llm_content = result["stdout"]
                        llm_file = log_dir / "llm.log"
                        llm_file.write_text(llm_content, encoding="utf-8")
                        saved_count += 1
                        llm_log_saved = True
                        logger.info("    ✓ Saved llm.log from %s (%d chars)", llm_path, len(llm_content))
                        break
                except Exception as e:
                    logger.debug("    Failed to check llm.log at %s: %s", llm_path, e)

            if not llm_log_saved:
                search_result = await self.container_manager.exec_in_container(
                    "find ~/.jiuwenswarm -name 'llm.log' -type f 2>/dev/null",
                    timeout=15
                )
                if search_result.get("returncode") == 0 and search_result.get("stdout"):
                    found_paths = [p for p in search_result["stdout"].strip().split("\n") if p]
                    if found_paths:
                        for found_path in found_paths:
                            try:
                                result = await self.container_manager.exec_in_container(
                                    f"cat {found_path} 2>/dev/null | tail -2000",
                                    timeout=10
                                )
                                if result.get("returncode") == 0 and result.get("stdout"):
                                    llm_content = result["stdout"]
                                    rel_parts = found_path.replace("/root/.jiuwenswarm/", "")\
                                        .replace("/home/", "").split("/")
                                    safe_name = "_".join(p for p in rel_parts if p) \
                                        if len(found_paths) > 1 else "llm.log"
                                    llm_file = log_dir / safe_name
                                    llm_file.write_text(llm_content, encoding="utf-8")
                                    saved_count += 1
                                    llm_log_saved = True
                                    logger.info("  ✓ Saved llm.log from %s (%d chars)", found_path, len(llm_content))
                            except Exception as e:
                                logger.debug("    Failed to save llm.log from %s: %s", found_path, e)

            if not llm_log_saved:
                logger.info("    llm.log not found in .jiuwenswarm, searching workspace directory...")
                ws_search_result = await self.container_manager.exec_in_container(
                    f"find {workspace} -name 'llm.log' -type f 2>/dev/null",
                    timeout=15
                )
                if ws_search_result.get("returncode") == 0 and ws_search_result.get("stdout"):
                    found_paths = [p for p in ws_search_result["stdout"].strip().split("\n") if p]
                    for found_path in found_paths:
                        try:
                            result = await self.container_manager.exec_in_container(
                                f"cat {found_path} 2>/dev/null | tail -2000",
                                timeout=10
                            )
                            if result.get("returncode") == 0 and result.get("stdout"):
                                llm_content = result["stdout"]
                                llm_file = log_dir / "llm.log"
                                llm_file.write_text(llm_content, encoding="utf-8")
                                saved_count += 1
                                llm_log_saved = True
                                logger.info("    ✓ Saved llm.log from %s (%d chars)", found_path, len(llm_content))
                        except Exception as e:
                            logger.debug("    Failed to save llm.log from %s: %s", found_path, e)

            if not llm_log_saved:
                logger.info("    Searching for openjiuwen-style LLM logs (jiuwen.log / jiuwen.jsonl)...")
                oj_search_paths = [
                    f"{workspace}/logs/run/jiuwen.log",
                    f"{workspace}/logs/run/jiuwen.jsonl",
                    "./logs/run/jiuwen.log",
                    "./logs/run/jiuwen.jsonl",
                    "/root/logs/run/jiuwen.log",
                    "/root/logs/run/jiuwen.jsonl",
                    "/app/logs/run/jiuwen.log",
                    "/app/logs/run/jiuwen.jsonl",
                ]
                for oj_path in oj_search_paths:
                    try:
                        result = await self.container_manager.exec_in_container(
                            f"cat {oj_path} 2>/dev/null | tail -2000",
                            timeout=10
                        )
                        if result.get("returncode") == 0 and result.get("stdout"):
                            oj_content = result["stdout"]
                            oj_name = oj_path.split("/")[-1]
                            oj_file = log_dir / f"llm_{oj_name}"
                            oj_file.write_text(oj_content, encoding="utf-8")
                            saved_count += 1
                            llm_log_saved = True
                            logger.info(
                                "  ✓ Saved LLM log from %s as llm_%s (%d chars)", oj_path, oj_name, len(oj_content))
                            break
                    except Exception as e:
                        logger.debug("    Failed to check %s: %s", oj_path, e)

                if not llm_log_saved:
                    logger.info("    No openjiuwen-style logs found, doing broad search...")
                    broad_search_dirs = [workspace, "/root", "."]
                    for search_dir in broad_search_dirs:
                        if llm_log_saved:
                            break
                        broad_result = await self.container_manager.exec_in_container(
                            f"find {search_dir} -path '*/logs/*' -name '*.log' -type f 2>/dev/null | head -20",
                            timeout=15
                        )
                        if broad_result.get("returncode") == 0 and broad_result.get("stdout"):
                            found_logs = [p for p in broad_result["stdout"].strip().split("\n") if p]
                            for found_log in found_logs:
                                log_basename = found_log.split("/")[-1]
                                if any(kw in log_basename.lower() for kw in ["llm", "jiuwen", "agent", "model"]):
                                    try:
                                        result = await self.container_manager.exec_in_container(
                                            f"cat {found_log} 2>/dev/null | tail -2000",
                                            timeout=10
                                        )
                                        if result.get("returncode") == 0 and result.get("stdout"):
                                            log_content = result["stdout"]
                                            save_name = f"llm_{log_basename}" \
                                            if "llm" not in log_basename else log_basename
                                            save_file = log_dir / save_name
                                            save_file.write_text(log_content, encoding="utf-8")
                                            saved_count += 1
                                            llm_log_saved = True
                                            logger.info("    ✓ Saved LLM-related log from %s as %s (%d chars)", 
                                                        found_log, save_name, len(log_content))
                                    except Exception as e:
                                        logger.debug("    Failed to save log from %s: %s", found_log, e)

            if not llm_log_saved:
                logger.warning("    llm.log not found anywhere in container")

            skills_log_dir = log_dir / "skills"
            evo_find_result = await self.container_manager.exec_in_container(
                f"find {self.agent_adapter.JIUWENSWARM_SKILL_DIR} "
                f"-type f \\( -name 'evolutions.json' -o -name '*.md' \\) 2>/dev/null",
                timeout=15
            )
            if evo_find_result.get("returncode") == 0 and evo_find_result.get("stdout", "").strip():
                evo_files = [f.strip() for f in evo_find_result["stdout"].strip().split("\n") if f.strip()]
                for evo_file in evo_files:
                    try:
                        rel_path = evo_file.replace(self.agent_adapter.JIUWENSWARM_SKILL_DIR + "/", "")
                        save_path = skills_log_dir / rel_path
                        save_path.parent.mkdir(parents=True, exist_ok=True)
                        cat_result = await self.container_manager.exec_in_container(
                            f"cat {evo_file} 2>/dev/null", timeout=10
                        )
                        if cat_result.get("returncode") == 0 and cat_result.get("stdout"):
                            save_path.write_text(cat_result["stdout"], encoding="utf-8")
                            saved_count += 1
                            logger.info("    ✓ Saved skills/%s (%d chars)", rel_path, len(cat_result["stdout"]))
                    except Exception as e:
                        logger.debug("    Failed to save skill file %s: %s", evo_file, e)

            if saved_count > 0:
                logger.info("    ✓ Saved %d files to %s", saved_count, log_dir)
            else:
                logger.warning("    No files found in container")

        except Exception as e:
            logger.error("    Error saving JiuWenSwarm logs: %s", e)

    async def _copy_task_files_to_container(self) -> None:
        workspace = self.config.workspace_dir

        tests_src = self.config.task_path / "tests"
        if tests_src.exists() and tests_src.is_dir():
            success = await self.container_manager.copy_to_container(
                tests_src,
                f"{workspace}/tests"
            )
            if success:
                logger.info("    ✓ Tests copied to %s/tests", workspace)
            else:
                logger.error("    Failed to copy tests")
        else:
            logger.warning("    Tests directory not found: %s", tests_src)

        workspace_src = self.config.task_path / "workspace"
        if workspace_src.exists() and workspace_src.is_dir():
            for item in workspace_src.iterdir():
                if item.is_file():
                    success = await self.container_manager.copy_to_container(
                        item,
                        f"{workspace}/{item.name}"
                    )
                    if not success:
                        logger.error("    Failed to copy %s", item.name)
                elif item.is_dir():
                    success = await self.container_manager.copy_to_container(
                        item,
                        f"{workspace}/{item.name}"
                    )
                    if not success:
                        logger.error("    Failed to copy %s", item.name)
            logger.info("    ✓ Workspace files copied")

        instruction_src = self.config.task_path / "instruction.md"
        if instruction_src.exists():
            success = await self.container_manager.copy_to_container(
                instruction_src,
                f"{workspace}/instruction.md"
            )
            if success:
                logger.info("    ✓ Instruction copied")

    def _load_instruction(self) -> str:
        instruction_path = self.config.task_path / "instruction.md"
        if instruction_path.exists():
            return instruction_path.read_text(encoding="utf-8")
        return "Please complete the task."

    async def _save_iteration_result(
        self,
        result: IterationResult,
        iteration: int,
        raw_output: str = "",
        stderr: str = ""
    ) -> None:
        iter_dir = self.output_dir / "iterations" / f"iteration_{iteration:03d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        if self.config.save_trajectory:
            trajectory_path = iter_dir / "trajectory.json"
            trajectory_path.write_text(
                json.dumps(result.agent_trajectory, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )

        output_path = iter_dir / "agent_output.txt"
        output_path.write_text(result.agent_output or "", encoding="utf-8")

        if raw_output:
            raw_output_path = iter_dir / "raw_output.txt"
            raw_output_path.write_text(raw_output, encoding="utf-8")

        if stderr:
            stderr_path = iter_dir / "stderr.txt"
            stderr_path.write_text(stderr, encoding="utf-8")

        test_path = iter_dir / "test_results.json"
        test_path.write_text(
            json.dumps(result.test_details, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        test_output = result.test_details.get("output", "")
        if test_output:
            test_output_path = iter_dir / "test_output.txt"
            test_output_path.write_text(test_output, encoding="utf-8")

        if result.skill_content:
            skill_path = iter_dir / "skill.md"
            skill_path.write_text(result.skill_content, encoding="utf-8")

        summary = {
            "iteration": result.iteration,
            "skill_hash": result.skill_hash,
            "execution_time": result.agent_execution_time,
            "tokens_used": result.agent_tokens_used,
            "test_passed": result.test_passed,
            "test_pass_rate": result.test_pass_rate,
            "skill_changed": result.skill_changed,
            "has_skill": result.skill_content is not None,
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat(),
            "evolution_events": result.evolution_events
        }

        summary_path = iter_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2),
            encoding="utf-8"
        )

        logger.info("  ✓ Results saved to %s", iter_dir)

    @staticmethod
    def _generate_evolution_suggestions(previous_result: IterationResult) -> str | None:
        suggestions = []
        pass_rate = previous_result.test_pass_rate

        if not previous_result.test_passed:
            test_details = previous_result.test_details
            failed_tests = test_details.get("failed_tests", [])
            test_output = test_details.get("output", "")

            specific_errors = extract_specific_errors(test_output)

            if specific_errors:
                suggestions.append("The following tests failed in the previous iteration:")
                for test_name, _error_detail in list(specific_errors.items())[:5]:
                    clean_name = test_name.split("::")[-1] if "::" in test_name else test_name
                    suggestions.append(f"  - {clean_name}")
            elif failed_tests:
                suggestions.append(f"Failed tests: {', '.join(failed_tests[:5])}")

            if test_output:
                assert_matches = re.findall(r"assert\s+.{1,200}", test_output)
                if assert_matches:
                    unique_asserts = list(dict.fromkeys(assert_matches))[:3]
                    suggestions.append("Key assertions that failed:")
                    for a in unique_asserts:
                        suggestions.append(f"  - `{a}`")

            suggestions.append("")
            suggestions.append("To fix these failures, you MUST:")
            suggestions.append("  1. Read the SKILL.md file for domain knowledge and code examples")
            suggestions.append("  2. Read the evolution/*.md files for troubleshooting tips from previous iterations")
            suggestions.append("  3. Check if any evolution experience directly addresses the failing test")

        if pass_rate < 0.5:
            suggestions.append(
                "- Major skill revision needed: less than 50% tests passing. "
                "Re-read the skill carefully and check all evolution experiences."
            )
        elif pass_rate < 0.8:
            suggestions.append(
                "- Some tests still failing. Check evolution experiences for targeted fixes."
            )
        elif pass_rate < 1.0:
            suggestions.append(
                "- Almost there. Check evolution experiences for fine-tuning tips."
            )

        if not previous_result.skill_changed:
            suggestions.append(
                "- Skill was not updated in previous iteration. "
                "Consider updating the skill based on what you learn from the evolution experiences."
            )

        if not suggestions:
            return None

        return "\n".join(suggestions)

    def _check_convergence(self) -> bool:
        if len(self.results) < self.config.convergence_threshold:
            return False

        recent_results = self.results[-self.config.convergence_threshold:]

        all_passed = all(r.test_passed for r in recent_results)
        if all_passed:
            return True

        if len(self.results) >= self.config.stagnation_patience:
            stagnation_window = self.results[-self.config.stagnation_patience:]
            pass_rates = [r.test_pass_rate for r in stagnation_window]
            if all(pr == pass_rates[0] for pr in pass_rates):
                any_skill_changed = any(r.skill_changed for r in stagnation_window)
                if not any_skill_changed:
                    return True

        return False

    def _calculate_metrics(self) -> dict[str, Any]:
        if not self.results:
            return {}

        initial_pass_rate = self.results[0].test_pass_rate
        final_pass_rate = self.results[-1].test_pass_rate

        total_tokens = sum(r.agent_tokens_used for r in self.results)
        total_time = sum(r.agent_execution_time for r in self.results)

        iterations_to_converge = len(self.results)
        convergence_type = "none"
        for i, result in enumerate(self.results):
            if result.test_passed:
                iterations_to_converge = i + 1
                convergence_type = "convergence"
                break

        if convergence_type == "none" and self._check_convergence():
            convergence_type = "deadlock_stagnation"

        return {
            "initial_pass_rate": initial_pass_rate,
            "final_pass_rate": final_pass_rate,
            "evolution_gain": final_pass_rate - initial_pass_rate,
            "total_iterations": len(self.results),
            "iterations_to_converge": iterations_to_converge,
            "convergence_type": convergence_type,
            "convergence_speed": iterations_to_converge / self.config.max_iterations,
            "total_tokens": total_tokens,
            "total_execution_time": total_time,
            "avg_pass_rate": sum(r.test_pass_rate for r in self.results) / len(self.results)
        }

    async def _generate_report(self, metrics: dict[str, Any]) -> Path:
        report_path = self.output_dir / "evolution_report.md"

        report_datetime = datetime.now(timezone.utc).astimezone()
        lines = [
            f"# Skill Evolution Report: {self.config.task_id}",
            "",
            f"**Agent**: {self.config.agent}",
            f"**Date**: {report_datetime.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"**Total Iterations**: {len(self.results)}",
            f"**Base Image**: {self.config.base_image}",
            "",
            "## Summary",
            "",
            f"- **Initial Pass Rate**: {metrics.get('initial_pass_rate', 0):.2%}",
            f"- **Final Pass Rate**: {metrics.get('final_pass_rate', 0):.2%}",
            f"- **Evolution Gain**: {metrics.get('evolution_gain', 0):+.2%}",
            f"- **Convergence**: {metrics.get('convergence_type', 'none')}",
            "",
            "## Iteration Details",
            ""
        ]

        for result in self.results:
            status = "✓ PASS" if result.test_passed else "✗ FAIL"
            lines.append(f"### Iteration {result.iteration}")
            lines.append("")
            lines.append(f"- **Status**: {status}")
            lines.append(f"- **Pass Rate**: {result.test_pass_rate:.2%}")
            lines.append(f"- **Execution Time**: {result.agent_execution_time:.2f}s")
            lines.append(f"- **Tokens Used**: {result.agent_tokens_used:,}")
            lines.append(f"- **Skill Changed**: {'Yes' if result.skill_changed else 'No'}")
            lines.append("")

        lines.extend([
            "## Metrics",
            "",
            f"- **Total Tokens**: {metrics.get('total_tokens', 0):,}",
            f"- **Total Execution Time**: {metrics.get('total_execution_time', 0):.2f}s",
            f"- **Average Pass Rate**: {metrics.get('avg_pass_rate', 0):.2%}",
            f"- **Convergence Type**: {metrics.get('convergence_type', 'none')}",
            f"- **Convergence Speed**: {metrics.get('convergence_speed', 0):.2%}",
            ""
        ])

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("\n✓ Report generated: %s", report_path)

        metrics_path = self.output_dir / "metrics.json"
        metrics_path.write_text(
            json.dumps(metrics, indent=2),
            encoding="utf-8"
        )

        return report_path


def _discover_tasks_by_category(tasks_dir: str, category_filter: str) -> list[str]:
    import tomllib

    tasks_path = Path(tasks_dir)
    if not tasks_path.exists():
        logger.error("Error: Tasks directory not found: %s", tasks_path)
        return []

    matched: list[str] = []
    for task_dir in sorted(tasks_path.iterdir()):
        if not task_dir.is_dir():
            continue
        toml_path = task_dir / "task.toml"
        if not toml_path.exists():
            continue
        try:
            with open(toml_path, "rb") as f:
                toml_data = tomllib.load(f)
            task_category = toml_data.get("category", "")
            if category_filter.lower() in task_category.lower():
                matched.append(task_dir.name)
        except Exception as e:
            logger.debug("Failed to parse %s: %s", toml_path, e)

    if not matched:
        logger.warning("No tasks found matching category '%s'", category_filter)
    else:
        logger.info("Found %d tasks matching '%s': %s", len(matched), category_filter, matched)

    return matched


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Evaluator Pipeline")
    parser.add_argument(
        "--config",
        type=str,
        help="Path to YAML configuration file (optional)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Override output directory"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        help="Override max iterations"
    )
    parser.add_argument(
        "--task-id",
        type=str,
        help="Task ID (single task)"
    )
    parser.add_argument(
        "--task-ids",
        type=str,
        help="Batch: comma-separated task IDs, e.g. 'citation-check,court-form-filling'"
    )
    parser.add_argument(
        "--tasks-dir",
        type=str,
        default="./tasks",
        help="Directory containing task folders (used with --task-ids), default: ./tasks"
    )
    parser.add_argument(
        "--tasks-filter",
        type=str,
        help="Batch: run all tasks whose task.toml category matches this filter, e.g. 'Office & White Collar'"
    )

    args = parser.parse_args()

    if args.config and Path(args.config).exists():
        base_config = PipelineConfig.from_yaml(Path(args.config))
    elif args.task_id or args.task_ids:
        overrides: dict = {}
        if args.output_dir:
            overrides["results_dir"] = Path(args.output_dir)
        if args.max_iterations:
            overrides["max_iterations"] = args.max_iterations
        base_config = PipelineConfig.from_args("__placeholder__", **overrides)
    else:
        logger.error("Error: No task specified. Use --task-id or --task-ids to run")
        logger.error("  Optionally use --config to load a YAML configuration file")
        sys.exit(1)

    if args.output_dir:
        base_config.results_dir = Path(args.output_dir)
    if args.max_iterations:
        base_config.max_iterations = args.max_iterations

    task_ids: list[str] = []

    if args.task_ids:
        task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()]
    elif args.tasks_filter:
        task_ids = _discover_tasks_by_category(args.tasks_dir, args.tasks_filter)
    elif args.task_id:
        task_ids = [args.task_id]
    elif base_config.task_id != "__placeholder__":
        task_ids = [base_config.task_id]
    else:
        logger.error("Error: No task specified. Use --task-id or --task-ids")
        sys.exit(1)

    if not task_ids:
        logger.error("Error: No tasks to run")
        sys.exit(1)

    logger.info("\n%s", "#" * 60)
    logger.info("  Evaluator Pipeline - Batch Mode")
    logger.info("  Tasks to run: %d", len(task_ids))
    for i, tid in enumerate(task_ids, 1):
        logger.info("    %d. %s", i, tid)
    logger.info("%s\n", "#" * 60)

    results_summary: list[dict] = []
    failed_tasks: list[str] = []

    for idx, task_id in enumerate(task_ids, 1):
        logger.info("\n%s", "=" * 60)
        logger.info("  Task [%d/%d]: %s", idx, len(task_ids), task_id)
        logger.info("%s\n", "=" * 60)

        config = base_config.with_task_id(task_id)
        pipeline = SkillEvolutionPipeline(config)

        try:
            result = await pipeline.run()

            summary = {
                "task_id": task_id,
                "iterations": result.total_iterations,
                "convergence": result.convergence_achieved,
                "convergence_type": result.metrics.get("convergence_type", "none"),
                "final_pass_rate": result.metrics.get("final_pass_rate", 0),
                "report": str(result.report_path),
            }
            results_summary.append(summary)

            conv_type = result.metrics.get("convergence_type", "none")
            conv_label = {
                "convergence": "✓ Converged", 
                "deadlock_stagnation": "⚠ Deadlock", 
                "none": "✗ No"
            }.get(conv_type, conv_type)
            logger.info("\n%s", "=" * 60)
            logger.info("  Task %s Complete!", task_id)
            logger.info("%s", "=" * 60)
            logger.info("  Iterations: %d", result.total_iterations)
            logger.info("  Convergence: %s", conv_label)
            logger.info("  Final Pass Rate: %.2f%%", result.metrics.get("final_pass_rate", 0) * 100)
            logger.info("  Report: %s", result.report_path)
            logger.info("%s\n", "=" * 60)

        except Exception as e:
            logger.error("\n  ✗ Task %s FAILED: %s", task_id, e)
            import traceback
            logger.error(traceback.format_exc())
            failed_tasks.append(task_id)
            results_summary.append({
                "task_id": task_id,
                "error": str(e),
            })

    if len(task_ids) > 1:
        logger.info("\n%s", "#" * 60)
        logger.info("  Batch Summary")
        logger.info("%s", "#" * 60)
        logger.info("  Total tasks: %d", len(task_ids))
        logger.info("  Succeeded: %d", len(task_ids) - len(failed_tasks))
        logger.info("  Failed: %d", len(failed_tasks))
        if failed_tasks:
            logger.info("  Failed tasks: %s", ", ".join(failed_tasks))
        logger.info("")
        for s in results_summary:
            if "error" in s:
                logger.error("  ✗ %s: ERROR - %s", s['task_id'], s['error'])
            else:
                rate = s['final_pass_rate']
                conv_type = s.get('convergence_type', 'none')
                conv_label = {"convergence": "✓", "deadlock_stagnation": "⚠", "none": "✗"}.get(conv_type, "?")
                logger.info(
                    "  %s %s: %.2f%% pass rate, %d iterations, %s", conv_label, s['task_id'], 
                    rate * 100, s['iterations'], conv_type)
        logger.info("%s\n", "#" * 60)

        summary_path = Path(base_config.results_dir) / "batch_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(results_summary, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info("  Batch summary saved to: %s\n", summary_path)

    if failed_tasks:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
