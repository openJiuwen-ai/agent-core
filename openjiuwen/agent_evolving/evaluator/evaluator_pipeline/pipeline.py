# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger

from .base import (
    BaseAgentAdapter,
    BaseBenchAdapter,
    create_agent,
    create_benchmark,
)
from .config import BuildConfigArgs, PipelineConfig
from .docker_env import DockerEnvironment
from .models import (
    AgentContext,
    EvalResult,
    IterationResult,
    PipelineResult,
    SkillDelta,
    Task,
)
from .skill_manager import SkillManager


def create_bench(name: str, config: dict[str, Any]) -> BaseBenchAdapter:
    return create_benchmark(name, config)








class EvolutionPipeline:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.agent = create_agent(config.agent, config.agent_config)
        self.bench = create_bench(config.benchmark, config.bench_config)
        self.skill_manager: SkillManager | None = None
        if config.evolution_mode:
            self.skill_manager = SkillManager(config)
        self._base_image_tag: str | None = None  

    async def run(self) -> list[PipelineResult]:
        logger.info(f"{'='*60}")
        logger.info(f"Initializing benchmark: {self.config.benchmark}")
        logger.info(f"{'='*60}")
        
        if not self.bench.clone_repo():
            logger.error("❌ Failed to clone benchmark repository")
            return []

        tasks = self.bench.load_tasks()

        if self.config.task_ids:
            tasks = [t for t in tasks if t.task_id in self.config.task_ids]

        if not tasks:
            logger.info("No tasks to run")
            return []

        logger.info(f"{'='*60}")
        mode_str = "EVOLUTION" if self.config.evolution_mode else "SINGLE-RUN"
        logger.info(f"Pipeline: {mode_str} mode | {len(tasks)} tasks")
        logger.info(f"Agent: {self.config.agent} | Benchmark: {self.config.benchmark}")
        
        if self.config.evolution_mode:
            logger.info(f"Max iterations: {self.config.max_iterations}")
            logger.info(f"Convergence: {'enabled' if self.config.convergence_check else 'disabled'}")
        logger.info(f"{'='*60}")

        results: list[PipelineResult] = []
        for idx, task in enumerate(tasks, 1):
            logger.info(f"[{idx}/{len(tasks)}] Task: {task.task_id}")
            try:
                result = await self.run_task(task)
                results.append(result)
            except Exception as e:
                logger.error(f"  ✗ Task failed: {e}")
                results.append(PipelineResult(
                    task_id=task.task_id,
                    agent_name=self.config.agent,
                    benchmark_name=self.config.benchmark,
                    total_iterations=0,
                    convergence_achieved=False,
                    convergence_type="error",
                    metrics={"error": str(e)},
                ))

        self._print_summary(results)
        self._save_results_summary(results)
        return results

    def _save_results_summary(self, results: list[PipelineResult]) -> None:
        """Save the final summary of all task results."""
        summary_dir = self.config.results_dir
        summary_dir.mkdir(parents=True, exist_ok=True)
        
        summary_data = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "agent": self.config.agent,
            "benchmark": self.config.benchmark,
            "evolution_mode": self.config.evolution_mode,
            "max_iterations": self.config.max_iterations,
            "total_tasks": len(results),
            "passed_tasks": sum(1 for r in results if r.convergence_achieved),
            "failed_tasks": sum(1 for r in results if not r.convergence_achieved),
            "tasks": [r.to_dict() for r in results],
        }
        
        summary_path = summary_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary_data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info(f"✓ Results summary saved to: {summary_path}")

    async def run_task(self, task: Task) -> PipelineResult:
        if self.config.evolution_mode:
            return await self._run_evolution(task)
        else:
            return await self._run_single(task)

    async def _run_single(self, task: Task) -> PipelineResult:
        env = await self._create_and_start_env(task)

        try:
            await self.bench.prepare_environment(task, env)

            setup_ok = await self.agent.setup(env)
            if not setup_ok:
                raise RuntimeError("Agent setup failed")

            if task.has_skills:
                await self._load_task_skills(env, task)

            logs_dir = self.config.results_dir / task.task_id
            logs_dir.mkdir(parents=True, exist_ok=True)
            self.agent.set_logs_dir(logs_dir)

            context = AgentContext(iteration=1, has_skill=task.has_skills)
            agent_result = await self.agent.run(env, task, context)

            # 立即保存 agent 输出，即使后续步骤失败也能保留轨迹
            if self.config.save_trajectory:
                (logs_dir / "agent_output.txt").write_text(
                    agent_result.raw_output or "", encoding="utf-8"
                )
                if agent_result.trajectory:
                    (logs_dir / "trajectory.json").write_text(
                        json.dumps(agent_result.trajectory, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8",
                    )

            eval_result = await self.bench.evaluate(env, task)

            iteration_result = IterationResult(
                iteration=1,
                agent_result=agent_result,
                eval_result=eval_result,
                skill_delta=SkillDelta(),
            )

            if self.config.save_trajectory:
                self._save_iteration_result(logs_dir, iteration_result)

            return PipelineResult(
                task_id=task.task_id,
                agent_name=self.config.agent,
                benchmark_name=self.config.benchmark,
                total_iterations=1,
                convergence_achieved=eval_result.passed,
                convergence_type="single_pass" if eval_result.passed else "single_fail",
                results=[iteration_result],
                metrics={"pass_rate": eval_result.pass_rate, "passed": eval_result.passed},
                output_dir=logs_dir,
            )
        finally:
            await env.stop()

    async def _run_evolution(self, task: Task) -> PipelineResult:
        if self.skill_manager is None:
            raise RuntimeError("skill_manager has not been initialized")
        self.skill_manager.init_for_task(task.task_id)

        env = await self._create_and_start_env(task)

        try:
            await self.bench.prepare_environment(task, env)

            setup_ok = await self.agent.setup(env)
            if not setup_ok:
                raise RuntimeError("Agent setup failed")

            logs_dir = self.config.results_dir / task.task_id
            logs_dir.mkdir(parents=True, exist_ok=True)
            self.agent.set_logs_dir(logs_dir)

            # In evolution mode, we don't load pre-defined skills initially.
            # The agent should create its own skill and evolve it.
            # We only load skills if they were captured from previous iterations.
            # Use verbose=False to suppress "Loaded X skills" message in evolution mode
            all_skills = self.skill_manager.load_all_skills(verbose=False)
            has_skill = bool(all_skills)

            if has_skill:
                # Check if skills are from previous iterations (have evolutions)
                has_evolutions = len(self.skill_manager.all_evolutions) > 0
                
                if has_evolutions:
                    # Load evolved skills from previous iterations
                    self.agent.set_skill_context(
                        self.skill_manager.resolved_skill_name,
                        self.skill_manager.get_all_skill_names(),
                    )
                    await self.agent.load_skills(
                        env, all_skills,
                        self.skill_manager.all_evolutions,
                        self.skill_manager.all_evolution_files,
                    )
                    logger.info(f"  ✓ Loaded {len(all_skills)} evolved skill(s) from previous iterations")
                else:
                    # First evolution run - let agent create skill from scratch
                    logger.info(f"  Evolution mode: Agent will create skill from scratch")

            iteration_results: list[IterationResult] = []
            convergence_achieved = False
            convergence_type = ""
            consecutive_no_change = 0

            for iteration in range(1, self.config.max_iterations + 1):
                logger.info(f"  --- Iteration {iteration}/{self.config.max_iterations} ---")

                evolution_suggestions = None
                if iteration > 1 and iteration_results:
                    prev = iteration_results[-1]
                    if not prev.eval_result.passed:
                        evolution_suggestions = self._build_evolution_suggestions(prev)

                context = AgentContext(
                    iteration=iteration,
                    has_skill=has_skill,
                    previous_result=iteration_results[-1] if iteration_results else None,
                    evolution_suggestions=evolution_suggestions,
                )

                agent_result = await self.agent.run(env, task, context)
                eval_result = await self.bench.evaluate(env, task)

                skill_delta = SkillDelta()
                skill_changed = False

                captured = await self.agent.capture_skills(env)
                if captured.changed:
                    skill_delta = captured
                    skill_changed = self.skill_manager.has_skill_changed(
                        captured.skills.get(
                            self.skill_manager.resolved_skill_name, ""
                        )
                    )

                    self.skill_manager.save_all_skills(
                        captured.skills,
                        iteration,
                        captured.evolutions,
                        captured.evolution_files,
                    )

                    for skill_name in captured.skills:
                        await self.skill_manager.render_evolution_to_skill_md_for(skill_name)

                    all_skills = self.skill_manager.load_all_skills()
                    has_skill = True

                    self.agent.set_skill_context(
                        self.skill_manager.resolved_skill_name,
                        self.skill_manager.get_all_skill_names(),
                    )
                    await self.agent.load_skills(
                        env, all_skills,
                        self.skill_manager.all_evolutions,
                        self.skill_manager.all_evolution_files,
                    )

                iter_result = IterationResult(
                    iteration=iteration,
                    agent_result=agent_result,
                    eval_result=eval_result,
                    skill_delta=skill_delta,
                    skill_changed=skill_changed,
                    started_at=datetime.now(tz=timezone.utc),
                    completed_at=datetime.now(tz=timezone.utc),
                )
                iteration_results.append(iter_result)

                if self.config.save_trajectory:
                    self._save_iteration_result(logs_dir, iter_result)

                logger.info(f"  Result: pass_rate={eval_result.pass_rate:.1%}, "
                      f"skill_changed={skill_changed}")

                if eval_result.passed:
                    convergence_achieved = True
                    convergence_type = "all_tests_pass"
                    logger.info(f"  ✓ All tests passed!")
                    break

                if self.config.convergence_check and not skill_changed:
                    consecutive_no_change += 1
                    if consecutive_no_change >= self.config.convergence_threshold:
                        convergence_achieved = False
                        convergence_type = "convergence_no_change"
                        logger.info(f"  Convergence: no skill change for {consecutive_no_change} iterations")
                        break
                else:
                    consecutive_no_change = 0

                if iteration >= self.config.stagnation_patience and not any(
                    r.eval_result.pass_rate > iteration_results[0].eval_result.pass_rate
                    for r in iteration_results[1:]
                ):
                    if iteration >= self.config.max_iterations:
                        convergence_type = "max_iterations"
                        logger.info(f"  Stagnation detected, stopping")
                        break

            if not convergence_type:
                convergence_type = "max_iterations"

            # Save agent-generated evolution.json file if captured
            if self.config.save_trajectory and self.agent.captured_evolution_json:
                for filename, content in self.agent.captured_evolution_json.items():
                    (logs_dir / filename).write_text(content, encoding="utf-8")
                    logger.info(f"  ✓ Saved {filename} from agent")

            return PipelineResult(
                task_id=task.task_id,
                agent_name=self.config.agent,
                benchmark_name=self.config.benchmark,
                total_iterations=len(iteration_results),
                convergence_achieved=convergence_achieved,
                convergence_type=convergence_type,
                results=iteration_results,
                metrics=self._compute_evolution_metrics(iteration_results),
                output_dir=logs_dir,
            )
        finally:
            await env.stop()

    async def _create_and_start_env(self, task: Task) -> DockerEnvironment:
        env_spec = task.environment_spec
        dockerfile = Path(env_spec.get("dockerfile", ""))
        build_context = Path(env_spec.get("build_context", ""))

        if not dockerfile.exists():
            raise FileNotFoundError(f"Dockerfile not found: {dockerfile}")

        source_config = self.agent.get_source_files()
        install_mode = source_config.get("mode") if source_config else None
        
        import tempfile
        temp_ctx = Path(tempfile.mkdtemp(prefix="evpipeline_ctx_"))
        
        for item in build_context.iterdir():
            if item.is_dir():
                shutil.copytree(item, temp_ctx / item.name, dirs_exist_ok=True)
            else:
                shutil.copy(item, temp_ctx / item.name)
        
        original_dockerfile_content = dockerfile.read_text(encoding="utf-8")
        
        # Debug: Check original Dockerfile content
        logger.info(f"  Original Dockerfile length: {len(original_dockerfile_content)} chars")
        if original_dockerfile_content:
            first_line = original_dockerfile_content.split("\n")[0][:50]
            logger.info(f"  Original Dockerfile first line: {repr(first_line)}")
        else:
            logger.warning(f"  ⚠️ Warning: Original Dockerfile is empty!")
        
        # Build base image with agent installed (cached)
        if install_mode in ("git", "pypi", "local") and not self._base_image_tag:
            await self._build_base_image(temp_ctx, original_dockerfile_content, source_config)
        
        # Build task-specific image (fast, based on cached base image)
        actual_dockerfile = await self._build_task_image(task, temp_ctx, original_dockerfile_content)
        
        image_tag = f"evpipeline_{task.task_id}:latest"
        env = DockerEnvironment(
            image_tag=image_tag,
            cpus=env_spec.get("cpus", 1),
            memory_mb=env_spec.get("memory_mb", 2048),
            timeout=env_spec.get("timeout", 900),
        )

        logger.info(f"  Building image: {image_tag}")
        build_timeout = 300  # Fast build since we're just copying task files
        
        # Use no_cache=True to ensure fresh build every time (for debugging)
        env.build(actual_dockerfile, temp_ctx, build_timeout=build_timeout, no_cache=True)

        logger.info(f"  Starting container...")
        await env.start()

        return env
    
    async def _build_base_image(self, temp_ctx: Path, original_dockerfile_content: str, source_config: dict):
        """Build a cached base image with agent installed"""
        install_mode = source_config.get("mode")
        packages = source_config.get("packages", [])
        
        # Copy local source code to build context for local mode
        if install_mode == "local":
            sources = source_config.get("sources", {})
            for pkg_name, src_path in sources.items():
                src_path = Path(src_path)
                if src_path.exists():
                    dest_path = temp_ctx / pkg_name
                    if dest_path.exists():
                        shutil.rmtree(dest_path)
                    shutil.copytree(src_path, dest_path)
                    logger.info(f"  Copied local source: {src_path} -> {dest_path}")
                else:
                    logger.warning(f"  ⚠️ Warning: Local source not found: {src_path}")
        
        pip_install_lines = []
        
        pip_install_lines.append('ARG PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple')
        pip_install_lines.append('ENV PIP_INDEX_URL=${PIP_MIRROR}')
        pip_install_lines.append('ENV PIP_TIMEOUT=120')
        pip_install_lines.append('ENV PIP_DEFAULT_TIMEOUT=120')
        
        pip_install_lines.append(
            'RUN if [ -f /etc/apt/sources.list ]; then '
            'sed -i "s|http://deb.debian.org|http://mirrors.aliyun.com|g" '
            '/etc/apt/sources.list; fi'
        )
        pip_install_lines.append(
            'RUN if [ -f /etc/apt/sources.list ]; then '
            'sed -i "s|http://security.debian.org|http://mirrors.aliyun.com|g" '
            '/etc/apt/sources.list; fi'
        )
        pip_install_lines.append(
            'RUN if [ -d /etc/apt/sources.list.d ]; then '
            'find /etc/apt/sources.list.d -type f -exec sed -i '
            '"s|http://deb.debian.org|http://mirrors.aliyun.com|g" {} \\;; fi'
        )
        pip_install_lines.append(
            'RUN if [ -d /etc/apt/sources.list.d ]; then '
            'find /etc/apt/sources.list.d -type f -exec sed -i '
            '"s|http://security.debian.org|http://mirrors.aliyun.com|g" {} \\;; fi'
        )
        
        pip_install_lines.append(
            'RUN apt-get update && apt-get install -y curl python3-pip '
            '&& rm -rf /var/lib/apt/lists/*'
        )
        pip_install_lines.append(
            'RUN python3 -m pip install --break-system-packages '
            '-i ${PIP_MIRROR} pytest==8.4.1 pytest-json-ctrf==0.3.5'
        )
        
        if install_mode == "git":
            pip_install_lines.append('RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*')
        
        for pkg in packages:
            pip_install_lines.append(f'RUN python3 -m pip install --break-system-packages -i ${{PIP_MIRROR}} "{pkg}"')
        
        # Handle local mode - copy source code and install
        if install_mode == "local":
            sources = source_config.get("sources", {})
            for pkg_name, src_path in sources.items():
                pip_install_lines.append(f'COPY {pkg_name}/ /opt/{pkg_name}/')
                pip_install_lines.append(f'RUN python3 -m pip install --break-system-packages -e /opt/{pkg_name}')
        
        # Install uv (modern Python package manager) using pip to leverage mirror
        pip_install_lines.append('RUN python3 -m pip install --break-system-packages -i ${PIP_MIRROR} uv==0.9.7')
        pip_install_lines.append('ENV EVOLUTION_AUTO_SCAN=true')
        
        logger.info(f"  Using {install_mode} mode, packages: {packages}")
        
        lines = original_dockerfile_content.split('\n')
        if lines:
            # Find the FROM line (ignore syntax directives and comments)
            insert_idx = -1
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('FROM '):
                    insert_idx = i + 1  # Insert AFTER FROM line
                    break
            
            if insert_idx == -1:
                # No FROM found, prepend our content with a FROM statement
                logger.warning(f"  ⚠️ Warning: No FROM directive found in original Dockerfile")
                base_content = (
                    f"FROM python:3.11-slim\n\n"
                    f"# === Auto-generated base image enhancements ===\n"
                    + "\n".join(pip_install_lines) + "\n\n"
                    + original_dockerfile_content
                )
            else:
                # Insert AFTER the FROM line
                lines.insert(
                    insert_idx,
                    '\n# === Auto-generated base image enhancements ===\n'
                    + '\n'.join(pip_install_lines)
                )
                base_content = '\n'.join(lines)
        else:
            base_content = (
                f"FROM python:3.11-slim\n\n"
                f"# === Auto-generated base image enhancements ===\n"
                + "\n".join(pip_install_lines)
            )
        
        base_content += '\n\n# === Base image setup complete ==='
        
        # Debug: Check if FROM is present
        if not base_content.strip().startswith('FROM'):
            logger.warning(f"  ⚠️ Warning: Generated base Dockerfile doesn't start with FROM!")
            logger.debug(f"  First 100 chars: {repr(base_content[:100])}")
        
        # Generate unique base image tag based on agent name, install mode, and packages
        # Note: Added timestamp for debugging - remove in production
        hash_content = f"{self.agent.name()}:{install_mode}:{','.join(sorted(packages))}:{int(time.time())}"
        pkg_hash = hashlib.md5(hash_content.encode()).hexdigest()[:8]
        self._base_image_tag = f"evpipeline_base:{pkg_hash}"
        
        logger.info(f"  Building base image ({self.agent.name()} cache): {self._base_image_tag}")
        
        (temp_ctx / "Dockerfile.base").write_text(base_content, encoding="utf-8")
        
        # Save Dockerfile for debugging
        debug_dockerfile = Path("debug_Dockerfile.base")
        debug_dockerfile.write_text(base_content, encoding="utf-8")
        logger.info(f"  Saved Dockerfile for debugging: {debug_dockerfile.absolute()}")
        
        base_env = DockerEnvironment(image_tag=self._base_image_tag)
        build_timeout = 1800
        
        pip_mirror = self.config.agent_config.get("pip_mirror", "https://pypi.tuna.tsinghua.edu.cn/simple")
        build_args = {"PIP_MIRROR": pip_mirror}
        
        base_env.build(temp_ctx / "Dockerfile.base", temp_ctx, build_timeout=build_timeout, build_args=build_args)
        logger.info(f"  ✅ Base image cached: {self._base_image_tag}")
    
    async def _build_task_image(self, task: Task, temp_ctx: Path, original_dockerfile_content: str) -> Path:
        """Build a task-specific image based on the cached base image"""
        pip_mirror = self.config.agent_config.get("pip_mirror", "https://pypi.tuna.tsinghua.edu.cn/simple")
        
        if self._base_image_tag:
            # Use cached base image as base - already has jiuwenswarm installed
            task_dockerfile = f"""FROM {self._base_image_tag}

# === Task-specific setup ===
ARG PIP_MIRROR={pip_mirror}
ENV PIP_INDEX_URL=${{PIP_MIRROR}}
ENV PIP_TIMEOUT=120
ENV PIP_DEFAULT_TIMEOUT=120
"""
        else:
            # Fallback: use original FROM line
            lines = original_dockerfile_content.split('\n')
            from_line = next((line for line in lines if line.startswith('FROM')), 'FROM python:3.12-slim')
            task_dockerfile = f"""{from_line}

# === Task-specific setup ===
ARG PIP_MIRROR={pip_mirror}
ENV PIP_INDEX_URL=${{PIP_MIRROR}}
ENV PIP_TIMEOUT=120
ENV PIP_DEFAULT_TIMEOUT=120

# Replace apt sources with Aliyun mirror
RUN if [ -f /etc/apt/sources.list ]; then \\
    sed -i "s|http://deb.debian.org|http://mirrors.aliyun.com|g" \\
    /etc/apt/sources.list; fi
RUN if [ -f /etc/apt/sources.list ]; then \\
    sed -i "s|http://security.debian.org|http://mirrors.aliyun.com|g" \\
    /etc/apt/sources.list; fi
RUN apt-get update && apt-get install -y curl python3-pip && \\
    rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --break-system-packages \\
    pytest==8.4.1 pytest-json-ctrf==0.3.5
"""
        
        # Add task-specific content from original Dockerfile (excluding FROM)
        # Keep pip install commands - they will use the mirror from ENV
        for line in original_dockerfile_content.split('\n'):
            if line.startswith('FROM'):
                continue
            if line.strip():
                task_dockerfile += line + '\n'
        
        # Ensure skills directory exists in build context to avoid COPY failure
        skills_ctx_dir = temp_ctx / "skills"
        if not skills_ctx_dir.exists():
            skills_ctx_dir.mkdir(parents=True, exist_ok=True)
        
        # Add jiuwenswarm setup
        task_dockerfile += '''
# === Jiuwenswarm workspace setup ===
RUN mkdir -p /root/.jiuwenswarm/agent/workspace/skills /workspace/tests /workspace/logs/verifier
COPY skills /root/.jiuwenswarm/agent/workspace/skills
'''
        
        task_dockerfile_path = temp_ctx / "Dockerfile.task"
        task_dockerfile_path.write_text(task_dockerfile, encoding="utf-8")
        
        return task_dockerfile_path

    async def _load_task_skills(self, env: DockerEnvironment, task: Task) -> None:
        skills_dir = Path(task.environment_spec.get("skills_dir", ""))
        if not skills_dir.exists():
            return

        loaded = await self.agent.load_skills_from_dir(env, skills_dir)
        if loaded:
            self.agent.set_skill_context(loaded[0], loaded)
            logger.info(f"  ✓ Loaded {len(loaded)} task skills")

    @staticmethod
    def _build_evolution_suggestions(prev_result: IterationResult) -> str:
        eval_r = prev_result.eval_result
        parts: list[str] = []

        if eval_r.passed:
            return "All tests passed in the previous iteration. No changes needed."

        parts.append(f"Previous iteration pass rate: {eval_r.pass_rate:.1%}")

        if eval_r.failed_tests:
            parts.append(f"Failed tests ({len(eval_r.failed_tests)}):")
            for t in eval_r.failed_tests[:5]:
                parts.append(f"  - {t}")

        if prev_result.skill_delta.changed:
            parts.append("Skills were modified in the previous iteration.")
            for skill_name in prev_result.skill_delta.skills:
                parts.append(f"  - Modified: {skill_name}")

        if prev_result.skill_changed:
            parts.append(
                "The skill content changed but tests still fail. "
                "Consider reviewing the skill for accuracy and completeness."
            )
        else:
            parts.append(
                "The skill was NOT modified in the previous iteration. "
                "Consider whether the skill needs updates to address the failing tests."
            )

        return "\n".join(parts)

    @staticmethod
    def _compute_evolution_metrics(results: list[IterationResult]) -> dict[str, Any]:
        if not results:
            return {}

        pass_rates = [r.eval_result.pass_rate for r in results]
        skill_changes = sum(1 for r in results if r.skill_changed)

        return {
            "final_pass_rate": pass_rates[-1],
            "best_pass_rate": max(pass_rates),
            "first_pass_rate": pass_rates[0],
            "improvement": pass_rates[-1] - pass_rates[0],
            "skill_changes": skill_changes,
            "total_iterations": len(results),
            "converged": results[-1].eval_result.passed,
        }

    @staticmethod
    def _save_iteration_result(logs_dir: Path, result: IterationResult) -> None:
        iter_dir = logs_dir / f"iteration_{result.iteration:03d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "iteration": result.iteration,
            "pass_rate": result.eval_result.pass_rate,
            "passed": result.eval_result.passed,
            "skill_changed": result.skill_changed,
            "execution_time": result.agent_result.execution_time,
            "tokens_used": result.agent_result.tokens_used,
            "failed_tests": result.eval_result.failed_tests,
            "started_at": result.started_at.isoformat() if result.started_at else None,
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
        }
        (iter_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Save agent raw output
        if result.agent_result.raw_output:
            (iter_dir / "agent_output.txt").write_text(
                result.agent_result.raw_output, encoding="utf-8"
            )

        # Save agent stderr
        if result.agent_result.stderr:
            (iter_dir / "agent_stderr.txt").write_text(
                result.agent_result.stderr, encoding="utf-8"
            )

        # Save test output
        if result.eval_result.test_output:
            (iter_dir / "test_output.txt").write_text(
                result.eval_result.test_output, encoding="utf-8"
            )

        # Save agent trajectory (detailed steps)
        if result.agent_result.trajectory:
            (iter_dir / "trajectory.json").write_text(
                json.dumps(result.agent_result.trajectory, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

        # Save evolution events (LLM interactions, skill changes, etc.)
        if result.agent_result.evolution_events:
            (iter_dir / "evolution_events.json").write_text(
                json.dumps(result.agent_result.evolution_events, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

        # Save agent metadata (LLM logs, API calls, etc.)
        if result.agent_result.metadata:
            (iter_dir / "agent_metadata.json").write_text(
                json.dumps(result.agent_result.metadata, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

        # Save LLM logs as separate files (direct file format, not nested in JSON)
        if result.agent_result.llm_logs:
            for log_name, log_content in result.agent_result.llm_logs.items():
                (iter_dir / log_name).write_text(log_content, encoding="utf-8")
                logger.info(f"  ✓ Saved LLM log: {log_name} ({len(log_content)} chars)")

        # Save skill delta (captured skills)
        if result.skill_delta.changed and result.skill_delta.skills:
            skills_dir = iter_dir / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            for skill_name, skill_content in result.skill_delta.skills.items():
                (skills_dir / f"{skill_name}.md").write_text(skill_content, encoding="utf-8")

        # Save evolution files if any
        if result.skill_delta.evolution_files:
            evo_dir = iter_dir / "evolution"
            evo_dir.mkdir(parents=True, exist_ok=True)
            for skill_name, files in result.skill_delta.evolution_files.items():
                skill_evo_dir = evo_dir / skill_name
                skill_evo_dir.mkdir(parents=True, exist_ok=True)
                for filename, content in files.items():
                    (skill_evo_dir / filename).write_text(content, encoding="utf-8")

    @staticmethod
    def _print_summary(results: list[PipelineResult]) -> None:
        logger.info(f"\n{'='*60}")
        logger.info("SUMMARY")
        logger.info(f"{'='*60}")

        for r in results:
            status = "✓ PASS" if r.convergence_achieved else "✗ FAIL"
            mode = f"({r.convergence_type})" if r.convergence_type else ""
            logger.info(f"  {r.task_id}: {status} {mode} "
                  f"iterations={r.total_iterations} "
                  f"pass_rate={r.metrics.get('final_pass_rate', r.metrics.get('pass_rate', 0)):.1%}")

        total = len(results)
        passed = sum(1 for r in results if r.convergence_achieved)
        logger.info(f"Total: {passed}/{total} tasks passed")


def build_default_config(args: BuildConfigArgs) -> PipelineConfig:
    results_dir = args.results_dir
    if results_dir is None:
        results_dir = "./evolution_results" if args.evolution_mode else "./single_run_results"
    return PipelineConfig(
        evolution_mode=args.evolution_mode,
        max_iterations=args.max_iterations if args.evolution_mode else 1,
        results_dir=Path(results_dir),
        task_ids=args.task_ids or [],
        agent_config={
            "api_key": args.api_key or "",
            "api_base": args.api_base or "",
            "model_name": args.model_name,
            "evolution_enabled": args.evolution_mode,
            "evolution_wait_time": args.evolution_wait_time,
            "agent_timeout": args.agent_timeout,
            "skill_persistence_dir": args.skill_persistence_dir,
        },
        bench_config={
            "tasks_dir": args.tasks_dir,
            "workspace_dir": args.workspace_dir,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evolution Pipeline v4")
    parser.add_argument("--config", type=str, help="Path to YAML config file")
    parser.add_argument("--agent", type=str, default=None, help="Agent adapter name")
    parser.add_argument("--benchmark", type=str, default=None, help="Benchmark adapter name")
    parser.add_argument("--tasks-dir", type=str, default="tasks", help="Tasks directory")
    parser.add_argument("--task-ids", type=str, nargs="+", default=[], help="Specific task IDs to run")
    parser.add_argument("--evolution", action="store_true", help="Enable evolution mode")
    parser.add_argument("--max-iterations", type=int, default=5, help="Max iterations (evolution mode)")
    parser.add_argument("--convergence-threshold", type=int, default=2, help="Convergence threshold")
    parser.add_argument("--stagnation-patience", type=int, default=3, help="Stagnation patience")
    parser.add_argument(
        "--results-dir", type=str, default=None,
        help="Results directory (default: ./evolution_results for evolution mode, "
             "./single_run_results for single run mode)"
    )
    parser.add_argument("--api-key", type=str, help="API key for LLM")
    parser.add_argument("--api-base", type=str, help="API base URL")
    parser.add_argument("--model", type=str, default="glm-5", help="Model name")
    parser.add_argument("--workspace-dir", type=str, default="/workspace", help="Container workspace dir")
    parser.add_argument("--evolution-wait", type=int, default=60, help="Evolution wait time (seconds)")
    parser.add_argument("--agent-timeout", type=int, default=880, help="Agent timeout (seconds)")
    parser.add_argument("--skill-dir", type=str, default="~/.jiuwenswarm/agent/workspace/skills",
                        help="Skill persistence directory")

    args = parser.parse_args()

    if args.config:
        config = PipelineConfig.from_yaml(Path(args.config))
        if args.task_ids:
            config.task_ids = args.task_ids
        if args.agent:
            config.agent = args.agent
        if args.benchmark:
            config.benchmark = args.benchmark
        # Always set evolution_enabled based on --evolution flag
        config.evolution_mode = args.evolution
        config.agent_config["evolution_enabled"] = args.evolution
        if args.max_iterations:
            config.max_iterations = args.max_iterations
        if args.results_dir:
            config.results_dir = Path(args.results_dir)
    else:
        build_args = BuildConfigArgs(
            tasks_dir=args.tasks_dir,
            api_key=args.api_key,
            api_base=args.api_base,
            model_name=args.model,
            evolution_mode=args.evolution,
            max_iterations=args.max_iterations,
            task_ids=args.task_ids,
            results_dir=args.results_dir,
            workspace_dir=args.workspace_dir,
            evolution_wait_time=args.evolution_wait,
            agent_timeout=args.agent_timeout,
            skill_persistence_dir=args.skill_dir,
        )
        config = build_default_config(build_args)
        config.agent = args.agent or "jiuwenswarm"
        config.benchmark = args.benchmark or "skillsbench"
        config.convergence_threshold = args.convergence_threshold
        config.stagnation_patience = args.stagnation_patience

    pipeline = EvolutionPipeline(config)
    asyncio.run(pipeline.run())


if __name__ == "__main__":
    main()
