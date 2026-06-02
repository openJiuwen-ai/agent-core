# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger

from ...base import BaseAgentAdapter, register_agent
from ...docker_env import DockerEnvironment
from ...models import AgentContext, AgentRunResult, SkillDelta, Task
from ...skill_manager import extract_specific_errors


@register_agent("jiuwenswarm")
class JiuWenSwarmAgent(BaseAgentAdapter):
    SKILL_DIR = "/root/.jiuwenswarm/agent/workspace/skills"
    CONFIG_DIR = "/root/.jiuwenswarm/config"
    WORKSPACE_DIR = "/workspace"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._resolved_skill_name: str = ""
        self._all_skill_names: list[str] = []
        self._captured_evolution_json: dict[str, str] = {}

    @property
    def captured_evolution_json(self) -> dict[str, str]:
        return self._captured_evolution_json

    @staticmethod
    def name() -> str:
        return "jiuwenswarm"

    def supported_skills_modes(self) -> list[str]:
        return ["create", "read", "evolve"]

    def default_model(self) -> str | None:
        return self._config.get("model_name", "glm-5")

    def validate_config(self) -> list[str]:
        errors = []
        if not self._config.get("api_key"):
            errors.append("api_key is required (set DASHSCOPE_API_KEY or OPENAI_API_KEY)")
        if not self._config.get("api_base"):
            errors.append("api_base is required")
        return errors

    def get_source_files(self) -> dict[str, Any] | None:
        install_mode = self._config.get("install_mode", "auto")
        jiuwenswarm_git_url = self._config.get(
            "jiuwenswarm_git_url", 
            "https://gitcode.com/openJiuwen/jiuwenswarm.git@develop"
        )
        
        if install_mode == "git":
            return {
                "mode": "git",
                "packages": [f"git+{jiuwenswarm_git_url}"],
                "requires_git": True
            }
        elif install_mode == "pypi":
            return {
                "mode": "pypi",
                "packages": ["jiuwenswarm"]
            }
        elif install_mode == "local":
            project_root = Path(__file__).parent.parent.parent.parent
            jiuwenswarm_src = project_root / "jiuwenswarm"
            if jiuwenswarm_src.exists():
                return {
                    "mode": "local",
                    "sources": {"jiuwenswarm": jiuwenswarm_src}
                }
            else:
                logger.warning(f"  Warning: local mode but jiuwenswarm source not found: {jiuwenswarm_src}")
                return {
                    "mode": "git",
                    "packages": [f"git+{jiuwenswarm_git_url}"]
                }
        else:  # auto mode
            project_root = Path(__file__).parent.parent.parent.parent
            jiuwenswarm_src = project_root / "jiuwenswarm"
            if jiuwenswarm_src.exists():
                return {
                    "mode": "local",
                    "sources": {"jiuwenswarm": jiuwenswarm_src}
                }
            else:
                return {
                    "mode": "git",
                    "packages": [f"git+{jiuwenswarm_git_url}"]
                }

    def set_skill_context(self, resolved_name: str, all_names: list[str]) -> None:
        self._resolved_skill_name = resolved_name
        self._all_skill_names = all_names

    async def setup(self, env: DockerEnvironment) -> bool:
        logger.info("Setting up JiuWenSwarm...")

        result = await env.exec(
            "python3 -c 'import jiuwenswarm; print(\"OK\")'",
            timeout=30,
        )
        if "OK" not in result.stdout:
            logger.warning("  ⚠ JiuWenSwarm not found in container")
            return False

        logger.info("  ✓ JiuWenSwarm verified")
        
        # Ensure uv is properly installed and available
        await env.exec("which uv || python3 -m pip install --break-system-packages uv==0.9.7", timeout=60)
        await env.exec("uv --version", timeout=30)
        logger.info("  ✓ uv verified")

        await env.exec(f"mkdir -p {self.CONFIG_DIR}", timeout=10)

        # Set pip mirror environment variables for the entire container session
        await env.exec("export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple", timeout=10)
        await env.exec("export PIP_TIMEOUT=120", timeout=10)
        await env.exec("export PIP_DEFAULT_TIMEOUT=120", timeout=10)
        
        api_base = self._config.get("api_base", "")
        api_key = self._config.get("api_key", "")
        model_name = self._config.get("model_name", "glm-5")
        evolution_enabled = self._config.get("evolution_enabled", True)

        env_content = f"""API_BASE={api_base}
API_KEY={api_key}
MODEL_NAME={model_name}
MODEL_PROVIDER={"DashScope" if "dashscope" in api_base else "openai"}

EVOLUTION_AUTO_SCAN={"true" if evolution_enabled else "false"}
EVOLUTION_AUTO_SAVE={"true" if evolution_enabled else "false"}

PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
PIP_TIMEOUT=120
PIP_DEFAULT_TIMEOUT=120
"""
        env_path = Path("/tmp/jiuwenswarm_env")
        env_path.write_text(env_content, encoding="utf-8")
        await env.copy_to(env_path, f"{self.CONFIG_DIR}/.env")
        if env_path.exists():
            env_path.unlink()
        logger.info(f"  ✓ Created .env file (evolution={'enabled' if evolution_enabled else 'disabled'})")

        config_yaml = f"""preferred_language: en

models:
  default:
    model_client_config:
      api_base: ${{API_BASE}}
      api_key: ${{API_KEY}}
      model_name: ${{MODEL_NAME:-{model_name}}}
      client_provider: ${{MODEL_PROVIDER:-openai}}
      timeout: 1800
      verify_ssl: false
      custom_headers: {{}}
    model_config_obj:
      temperature: 0.7

sandbox:
  enabled: false

react:
  max_iterations: 50
  evolution:
    enabled: {"true" if evolution_enabled else "false"}
    auto_scan: {"true" if evolution_enabled else "false"}
    auto_save: {"true" if evolution_enabled else "false"}
    skill_base_dir: /root/.jiuwenswarm/agent/workspace/skills

memory:
  engine: none
"""
        config_path = Path("/tmp/jiuwenswarm_config.yaml")
        config_path.write_text(config_yaml, encoding="utf-8")
        await env.copy_to(config_path, f"{self.CONFIG_DIR}/config.yaml")
        if config_path.exists():
            config_path.unlink()
        logger.info(f"  ✓ Created config.yaml (evolution={'enabled' if evolution_enabled else 'disabled'})")

        return True

    async def load_skills(
        self,
        env: DockerEnvironment,
        skills: dict[str, str],
        evolutions: dict[str, str] | None = None,
        evolution_files: dict[str, dict[str, str]] | None = None,
    ) -> int:
        evolutions = evolutions or {}
        evolution_files = evolution_files or {}
        loaded = 0

        for skill_name, skill_content in skills.items():
            ok = await self._load_single_skill(
                env, skill_name, skill_content,
                evolutions.get(skill_name),
                evolution_files.get(skill_name),
            )
            if ok:
                loaded += 1

        self._all_skill_names = list(skills.keys())
        if skills and not self._resolved_skill_name:
            self._resolved_skill_name = next(iter(skills))

        logger.info(f"  Loaded {loaded}/{len(skills)} skills into container")
        return loaded

    async def _load_single_skill(
        self,
        env: DockerEnvironment,
        skill_name: str,
        skill_content: str,
        evo_content: str | None = None,
        evo_files: dict[str, str] | None = None,
    ) -> bool:
        skill_dir = f"{self.SKILL_DIR}/{skill_name}"
        await env.exec(f"mkdir -p {skill_dir}", timeout=10)

        skill_path = Path(f"/tmp/skill_{skill_name}.md")
        skill_path.write_text(skill_content, encoding="utf-8")
        success = await env.copy_to(skill_path, f"{skill_dir}/SKILL.md")
        if skill_path.exists():
            skill_path.unlink()

        if not success:
            logger.error(f"  Failed to load skill: {skill_name}")
            return False

        logger.info(f"  Skill loaded: {skill_dir}/SKILL.md")

        if evo_content:
            evo_path = Path(f"/tmp/evolutions_{skill_name}.json")
            evo_path.write_text(evo_content, encoding="utf-8")
            evo_success = await env.copy_to(evo_path, f"{skill_dir}/evolutions.json")
            if evo_success:
                logger.info(f"  Evolutions loaded: {skill_dir}/evolutions.json ({len(evo_content)} chars)")
            if evo_path.exists():
                evo_path.unlink()

        if evo_files:
            evolution_dir = f"{skill_dir}/evolution"
            await env.exec(f"mkdir -p {evolution_dir}", timeout=10)
            for filename, file_content in evo_files.items():
                file_path = Path(f"/tmp/evolution_{skill_name}_{filename}")
                file_path.write_text(file_content, encoding="utf-8")
                file_success = await env.copy_to(file_path, f"{evolution_dir}/{filename}")
                if file_success:
                    logger.info(f"  Evolution file loaded: {evolution_dir}/{filename}")
                if file_path.exists():
                    file_path.unlink()

        return True

    async def load_skills_from_dir(
        self,
        env: DockerEnvironment,
        skills_dir: Path,
    ) -> list[str]:
        if not skills_dir.exists() or not skills_dir.is_dir():
            logger.warning(f"  No skills directory found: {skills_dir}")
            return []

        loaded_skills: list[str] = []
        for skill_subdir in sorted(skills_dir.iterdir()):
            if not skill_subdir.is_dir():
                continue
            skill_md = skill_subdir / "SKILL.md"
            if not skill_md.exists():
                continue

            skill_name = skill_subdir.name
            container_skill_dir = f"{self.SKILL_DIR}/{skill_name}"
            await env.exec(f"mkdir -p {container_skill_dir}", timeout=10)

            tmp_path = Path(f"/tmp/skill_{skill_name}.md")
            tmp_path.write_text(skill_md.read_text(encoding="utf-8"), encoding="utf-8")
            success = await env.copy_to(tmp_path, f"{container_skill_dir}/SKILL.md")
            if tmp_path.exists():
                tmp_path.unlink()

            if success:
                loaded_skills.append(skill_name)
                logger.info(f"    ✓ Loaded skill: {skill_name}")

            for extra in skill_subdir.iterdir():
                if extra.is_file() and extra.name != "SKILL.md":
                    tmp_extra = Path(f"/tmp/skill_{skill_name}_{extra.name}")
                    tmp_extra.write_text(extra.read_text(encoding="utf-8"), encoding="utf-8")
                    await env.copy_to(tmp_extra, f"{container_skill_dir}/{extra.name}")
                    if tmp_extra.exists():
                        tmp_extra.unlink()

        self._all_skill_names = loaded_skills
        if loaded_skills:
            self._resolved_skill_name = loaded_skills[0]

        return loaded_skills

    async def run(
        self,
        env: DockerEnvironment,
        task: Task,
        context: AgentContext,
    ) -> AgentRunResult:
        iteration = context.iteration
        has_skill = context.has_skill
        evolution_suggestions = context.evolution_suggestions
        previous_result = context.previous_result

        logger.info(f"Running JiuWenSwarm (iteration {iteration})...")
        if has_skill:
            logger.info(f"  Using existing skill from previous iteration")
            if evolution_suggestions:
                logger.info(f"  Evolution suggestions provided")
        else:
            if self._config.get("evolution_enabled", False):
                logger.info(f"  No existing skill, will create new skill")
            else:
                logger.info(f"  Single-run mode: executing task without skill evolution")

        instruction_path = Path("/tmp/instruction.txt")
        instruction_path.write_text(task.instruction, encoding="utf-8")
        await env.copy_to(instruction_path, "/tmp/jiuwenswarm_instruction.txt")

        system_message = self._build_system_message(
            iteration, has_skill, evolution_suggestions, previous_result
        )
        if system_message:
            system_path = Path("/tmp/system_message.txt")
            system_path.write_text(system_message, encoding="utf-8")
            await env.copy_to(system_path, "/tmp/jiuwenswarm_system_message.txt")
            if system_path.exists():
                system_path.unlink()

        runner_script = _get_runner_script()
        runner_path = Path("/tmp/jiuwenswarm_runner.py")
        runner_path.write_text(runner_script, encoding="utf-8")
        await env.copy_to(runner_path, "/tmp/jiuwenswarm_runner.py")

        if runner_path.exists():
            runner_path.unlink()
        if instruction_path.exists():
            instruction_path.unlink()

        start_time = time.time()
        evolution_wait = self._config.get("evolution_wait_time", 60) if has_skill else 0
        agent_timeout = self._config.get("agent_timeout", 880)

        result = await env.exec(
            f"JIUWENSWARM_EVOLUTION_WAIT={evolution_wait} "
            f"JIUWENSWARM_AGENT_TIMEOUT={agent_timeout} "
            "python3 /tmp/jiuwenswarm_runner.py",
            timeout=agent_timeout + evolution_wait + 30,
        )
        execution_time = time.time() - start_time

        raw_output = result.stdout
        stderr = result.stderr

        debug_dir = Path("/tmp/jiuwenswarm_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "raw_output.txt").write_text(raw_output, encoding="utf-8")
        if stderr:
            (debug_dir / "stderr.txt").write_text(stderr, encoding="utf-8")

        trajectory = []
        final_response = ""
        tokens_used = 0
        evolution_events = []
        metadata = {}

        parsed = _parse_output(raw_output)
        if parsed:
            trajectory = parsed.get("messages", [])
            final_response = parsed.get("final_response", "")
            evolution_events = parsed.get("evolution_events", [])
            metadata = parsed.get("metadata", {})
            tokens_used = _estimate_tokens(trajectory)
            logger.info(f"  Parsed trajectory: {len(trajectory)} messages")
            if evolution_events:
                logger.info(f"  Evolution events: {len(evolution_events)}")
            if metadata:
                logger.info(f"  Metadata captured: {len(metadata.keys())} keys")
        else:
            logger.warning(f"  Warning: Failed to parse JiuWenSwarm output")
            logger.debug(f"  Raw output length: {len(raw_output)} chars")
            logger.debug(f"  Stderr length: {len(stderr)} chars")

        # Capture LLM logs from container (referencing v2 implementation)
        logs_info = {}
        llm_logs_found = {}
        
        # Search for LLM logs in common paths
        llm_search_paths = [
            "./logs/llm.log",
            f"{self.WORKSPACE_DIR}/logs/llm.log",
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
                result = await env.exec(
                    f"cat {llm_path} 2>/dev/null | tail -4000",
                    timeout=10
                )
                if result.success and result.stdout.strip():
                    llm_content = result.stdout
                    llm_logs_found["llm.log"] = llm_content
                    logger.info(f"  ✓ Found llm.log from {llm_path} ({len(llm_content)} chars)")
                    break
            except Exception as e:
                logger.warning(f"  ⚠ Failed to check llm.log at {llm_path}: {e}")

        # If not found, search in .jiuwenswarm directory
        if "llm.log" not in llm_logs_found:
            search_result = await env.exec(
                "find ~/.jiuwenswarm -name 'llm.log' -type f 2>/dev/null",
                timeout=15
            )
            if search_result.success and search_result.stdout.strip():
                found_paths = [p.strip() for p in search_result.stdout.strip().split("\n") if p.strip()]
                for found_path in found_paths[:3]:  # Limit to 3 files
                    try:
                        result = await env.exec(
                            f"cat {found_path} 2>/dev/null | tail -4000",
                            timeout=10
                        )
                        if result.success and result.stdout.strip():
                            llm_content = result.stdout
                            rel_parts = found_path.replace("/root/.jiuwenswarm/", "").replace("/home/", "").split("/")
                            safe_name = "_".join(p for p in rel_parts if p) if len(found_paths) > 1 else "llm.log"
                            llm_logs_found[safe_name] = llm_content
                            logger.info(f"  ✓ Found llm.log from {found_path} ({len(llm_content)} chars)")
                    except Exception as e:
                        logger.warning(f"  ⚠ Failed to save llm.log from {found_path}: {e}")

        # If not found, search in workspace directory
        if "llm.log" not in llm_logs_found:
            ws_search_result = await env.exec(
                f"find {self.WORKSPACE_DIR} -name 'llm.log' -type f 2>/dev/null",
                timeout=15
            )
            if ws_search_result.success and ws_search_result.stdout.strip():
                found_paths = [p.strip() for p in ws_search_result.stdout.strip().split("\n") if p.strip()]
                for found_path in found_paths[:3]:
                    try:
                        result = await env.exec(
                            f"cat {found_path} 2>/dev/null | tail -4000",
                            timeout=10
                        )
                        if result.success and result.stdout.strip():
                            llm_content = result.stdout
                            llm_logs_found["llm.log"] = llm_content
                            logger.info(f"  ✓ Found llm.log from {found_path} ({len(llm_content)} chars)")
                    except Exception as e:
                        logger.warning(f"  ⚠ Failed to save llm.log from {found_path}: {e}")

        # Search for openjiuwen-style LLM logs (jiuwen.log / jiuwen.jsonl)
        if not llm_logs_found:
            oj_search_paths = [
                f"{self.WORKSPACE_DIR}/logs/run/jiuwen.log",
                f"{self.WORKSPACE_DIR}/logs/run/jiuwen.jsonl",
                "./logs/run/jiuwen.log",
                "./logs/run/jiuwen.jsonl",
                "/root/logs/run/jiuwen.log",
                "/root/logs/run/jiuwen.jsonl",
                "/app/logs/run/jiuwen.log",
                "/app/logs/run/jiuwen.jsonl",
            ]
            for oj_path in oj_search_paths:
                try:
                    result = await env.exec(
                        f"cat {oj_path} 2>/dev/null | tail -4000",
                        timeout=10
                    )
                    if result.success and result.stdout.strip():
                        oj_content = result.stdout
                        oj_name = oj_path.split("/")[-1]
                        llm_logs_found[f"llm_{oj_name}"] = oj_content
                        logger.info(f"  ✓ Found LLM log from {oj_path} as llm_{oj_name} ({len(oj_content)} chars)")
                        break
                except Exception as e:
                    logger.warning(f"  ⚠ Failed to check {oj_path}: {e}")

        # Broad search for LLM-related logs
        if not llm_logs_found:
            broad_search_dirs = [self.WORKSPACE_DIR, "/root", "."]
            for search_dir in broad_search_dirs:
                if llm_logs_found:
                    break
                broad_result = await env.exec(
                    f"find {search_dir} -path '*/logs/*' -name '*.log' -type f 2>/dev/null | head -20",
                    timeout=15
                )
                if broad_result.success and broad_result.stdout.strip():
                    found_logs = [p.strip() for p in broad_result.stdout.strip().split("\n") if p.strip()]
                    for found_log in found_logs:
                        log_basename = found_log.split("/")[-1]
                        if any(kw in log_basename.lower() for kw in ["llm", "jiuwen", "agent", "model"]):
                            try:
                                result = await env.exec(
                                    f"cat {found_log} 2>/dev/null | tail -4000",
                                    timeout=10
                                )
                                if result.success and result.stdout.strip():
                                    log_content = result.stdout
                                    save_name = f"llm_{log_basename}" if "llm" not in log_basename else log_basename
                                    llm_logs_found[save_name] = log_content
                                    logger.info(
                                        f"  ✓ Found LLM-related log from {found_log} "
                                        f"as {save_name} ({len(log_content)} chars)"
                                    )
                            except Exception as e:
                                logger.warning(f"  ⚠ Failed to save log from {found_log}: {e}")

        if not llm_logs_found:
            logger.warning(f"  ⚠ llm.log not found anywhere in container")
        
        # Get jiuwenswarm debug logs
        log_result = await env.exec(
            f"cat /tmp/jiuwenswarm_debug/*.txt 2>/dev/null || echo 'No debug logs found'",
            timeout=30,
        )
        if log_result.stdout and "No debug logs found" not in log_result.stdout:
            logs_info["debug_logs"] = log_result.stdout
        
        # Add LLM logs to metadata
        if llm_logs_found:
            logs_info["llm"] = llm_logs_found
        
        # Add LLM logs to metadata for backward compatibility
        if logs_info:
            metadata["logs"] = logs_info

        # Return LLM logs separately for direct file saving
        llm_logs = llm_logs_found if llm_logs_found else None

        return AgentRunResult(
            final_response=final_response,
            trajectory=trajectory,
            execution_time=execution_time,
            tokens_used=tokens_used,
            raw_output=raw_output,
            stderr=stderr,
            evolution_events=evolution_events,
            metadata=metadata,
            llm_logs=llm_logs,
        )

    async def capture_skills(self, env: DockerEnvironment) -> SkillDelta:
        logger.info("Capturing created skills...")

        evolution_contents: dict[str, str] = {}
        
        # Capture evolutions.json (plural, for skill evolutions)
        evo_result = await env.exec(
            f"find {self.SKILL_DIR} -name 'evolutions.json' 2>/dev/null",
            timeout=30,
        )

        if evo_result.success and evo_result.stdout.strip():
            evo_files = [f.strip() for f in evo_result.stdout.strip().split("\n") if f.strip()]
            logger.info(f"  Found {len(evo_files)} evolutions.json files")
            for evo_file in evo_files:
                skill_name = Path(evo_file).parent.name
                read_result = await env.exec(f"cat {evo_file}", timeout=30)
                if read_result.success:
                    evolution_contents[skill_name] = read_result.stdout
                    logger.info(f"  {skill_name}/evolutions.json: {len(read_result.stdout)} chars")

        # Capture evolution.json (singular, generated by jiuwenswarm during evolution)
        self._captured_evolution_json.clear()
        evo_json_result = await env.exec(
            f"find {self.WORKSPACE_DIR} -name 'evolution.json' 2>/dev/null",
            timeout=30,
        )
        if evo_json_result.success and evo_json_result.stdout.strip():
            evo_json_files = [f.strip() for f in evo_json_result.stdout.strip().split("\n") if f.strip()]
            logger.info(f"  Found {len(evo_json_files)} evolution.json files")
            for evo_json_file in evo_json_files:
                read_result = await env.exec(f"cat {evo_json_file}", timeout=30)
                if read_result.success:
                    filename = Path(evo_json_file).name
                    self._captured_evolution_json[filename] = read_result.stdout
                    logger.info(f"  Captured {evo_json_file}: {len(read_result.stdout)} chars")

        captured_evolution_files: dict[str, dict[str, str]] = {}
        evo_dir_result = await env.exec(
            f"find {self.SKILL_DIR} -path '*/evolution/*.md' 2>/dev/null",
            timeout=30,
        )

        if evo_dir_result.success and evo_dir_result.stdout.strip():
            evo_md_files = [f.strip() for f in evo_dir_result.stdout.strip().split("\n") if f.strip()]
            logger.info(f"  Found {len(evo_md_files)} evolution/*.md files")
            for md_file in evo_md_files:
                skill_name = Path(md_file).parent.parent.name
                filename = Path(md_file).name
                read_result = await env.exec(f"cat {md_file}", timeout=30)
                if read_result.success:
                    if skill_name not in captured_evolution_files:
                        captured_evolution_files[skill_name] = {}
                    captured_evolution_files[skill_name][filename] = read_result.stdout
                    logger.info(f"  {skill_name}/evolution/{filename}: {len(read_result.stdout)} chars")

        result = await env.exec(
            f"find {self.SKILL_DIR} -name 'SKILL.md' 2>/dev/null",
            timeout=30,
        )

        if not result.success or not result.stdout.strip():
            return SkillDelta(
                evolutions=evolution_contents,
                evolution_files=captured_evolution_files,
            )

        skill_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]

        created_skills: dict[str, str] = {}
        for skill_file in skill_files:
            skill_name = Path(skill_file).parent.name
            read_result = await env.exec(f"cat {skill_file}", timeout=30)
            if read_result.success:
                created_skills[skill_name] = read_result.stdout

        changed = len(created_skills) > 0 or len(evolution_contents) > 0 or len(captured_evolution_files) > 0

        logger.info(f"Captured {len(created_skills)} skills, {len(evolution_contents)} evolutions, "
              f"{sum(len(files) for files in captured_evolution_files.values())} evolution files")

        return SkillDelta(
            skills=created_skills,
            evolutions=evolution_contents,
            evolution_files=captured_evolution_files,
            changed=changed,
        )

    def _build_system_message(
        self,
        iteration: int,
        has_skill: bool = False,
        evolution_suggestions: str | None = None,
        previous_result: Any = None,
    ) -> str:
        parts = [self._base_prompt()]

        if not has_skill:
            parts.append(self._skill_creation_prompt())
        else:
            parts.append(self._skill_reading_prompt(evolution_suggestions))

        if previous_result is not None and hasattr(previous_result, "eval_result"):
            if not previous_result.eval_result.passed:
                parts.append(self._test_feedback_prompt(previous_result))

        return "\n".join(parts)

    @staticmethod
    def _base_prompt() -> str:
        return """You are an AI assistant tasked with solving command-line tasks in a Linux environment.

## Response Format

Structure your responses clearly with these sections:

1. **Analysis**: What is the current state? What has been accomplished?
2. **Plan**: What will you do next? Be specific about expected outcomes.
3. **Actions**: What commands will you execute?
4. **Status**: Is the task complete or in progress?

## Command Execution Guidelines

- End bash commands with a newline to execute them
- Use appropriate wait times:
  - 0.1s: Quick commands (ls, cat, cd, echo)
  - 1-5s: Moderate commands (pip install, git clone, npm install)
  - 10s+: Slow commands (make, compilation, large downloads)
- Use Ctrl+C (C-c) to interrupt stuck processes
- Use '&&' to chain dependent commands
- Use '2>&1' to capture stderr along with stdout

## Error Handling

When encountering errors:
1. Read error messages carefully
2. Check if dependencies are installed (use 'which' or '--version')
3. Verify file paths and permissions
4. Try alternative approaches
5. If stuck, explain what you've tried and what's blocking

## Task Completion

Before marking task complete:
1. Verify all requirements are met
2. Check output files exist and are valid
3. Run any provided tests if available
4. Include "TASK COMPLETE" in your final response when done
"""

    @staticmethod
    def _skill_creation_prompt() -> str:
        return """

## CRITICAL: Create Skills Before Solving

You must create skill documents that capture domain knowledge needed for this task.

### Skill Creation Process

1. **Analyze the task** - What knowledge is needed?
2. **Create focused skills** - 1-3 skills (quality over quantity)
3. **Use bash commands to create skill files**:

```bash
mkdir -p ~/.jiuwenswarm/agent/workspace/skills/<skill-name>

cat > ~/.jiuwenswarm/agent/workspace/skills/<skill-name>/SKILL.md << 'EOF'
---
name: <skill-name>
description: <what this skill does in one line>
---
# <Skill Title>

## Overview
<Brief description>

## Steps
1. <Step 1 with explanation>
2. <Step 2 with explanation>

## Code Examples
```language
<example code>
```

## Common Pitfalls
- <Pitfall 1 and how to avoid>
- <Pitfall 2 and how to avoid>
EOF
```

**IMPORTANT**: Choose a descriptive skill name that reflects the skill's purpose.

### After Creating Skills

1. **Verify**: Check the skill file exists
2. **Use**: Follow the skill's guidance to solve the task
3. **Iterate**: Update skills if you find better approaches
"""

    def _skill_reading_prompt(self, evolution_suggestions: str | None = None) -> str:
        parts: list[str] = []

        if evolution_suggestions:
            parts.append(f"""

## Evolution Suggestions from Previous Iteration

Based on the previous execution, the following improvements are recommended:

{evolution_suggestions}

You MUST address these suggestions by reading the skill and its evolution experiences.
""")

        all_skill_names = self._all_skill_names or [self._resolved_skill_name]
        if len(all_skill_names) == 1:
            parts.append(self._single_skill_reading_prompt())
        else:
            parts.append(self._multi_skill_reading_prompt(all_skill_names))

        return "\n".join(parts)

    def _single_skill_reading_prompt(self) -> str:
        return f"""

## CRITICAL: Read Skill Before Solving

A skill has been loaded for this task. You MUST read it before starting any work.

**Step 1**: Read the skill document:
```bash
cat ~/.jiuwenswarm/agent/workspace/skills/{self._resolved_skill_name}/SKILL.md
```

**Step 2**: Read the evolution files for troubleshooting tips:
```bash
cat ~/.jiuwenswarm/agent/workspace/skills/{self._resolved_skill_name}/evolution/*.md
```

**Step 3**: Follow the skill's guidance and the evolution experiences to solve the task.

**Step 4**: After solving, update the skill based on test failures and new insights.

**Evolution is enabled**: The skill will be automatically evolved based on your execution experience.

**WARNING**: If you see an Experience Index in SKILL.md but do NOT read the linked
evolution files, you will miss critical details such as exact commands, parameter values,
and error workarounds.
"""

    def _multi_skill_reading_prompt(self, all_skill_names: list[str]) -> str:
        skill_list_lines = []
        for sn in all_skill_names:
            skill_list_lines.append(f"  - `{sn}`: ~/.jiuwenswarm/agent/workspace/skills/{sn}/SKILL.md")
        skill_list = "\n".join(skill_list_lines)

        return f"""

## CRITICAL: Read ALL Skills Before Solving

{len(all_skill_names)} skills have been loaded for this task:

{skill_list}

**Step 1**: Read ALL skill documents and their evolution files.

**Step 2**: Follow ALL skills' guidance and evolution experiences to solve the task.

**Step 3**: After solving, update skills based on test failures and new insights.

**Evolution is enabled**: Skills will be automatically evolved based on your execution experience.

**WARNING**: If you see an Experience Index in SKILL.md but do NOT read the linked
evolution files, you will miss critical details.
"""

    @staticmethod
    def _test_feedback_prompt(previous_result: Any) -> str:
        eval_result = previous_result.eval_result
        pass_rate = eval_result.pass_rate
        failed_tests = eval_result.failed_tests
        test_output = eval_result.test_output

        specific_errors = extract_specific_errors(test_output)

        feedback = f"""

## Previous Iteration Test Results

**The previous iteration did NOT pass all tests.** Pass rate: {pass_rate * 100:.1f}%.

**Failed Tests**: {len(failed_tests)}
"""
        if specific_errors:
            feedback += "\n**Specific Failure Details**:\n"
            for test_name, error_detail in list(specific_errors.items())[:5]:
                feedback += f"\n### {test_name}\n```\n{error_detail}\n```\n"
        elif failed_tests:
            feedback += "\n**Failed Test Cases**:\n"
            for test in failed_tests[:5]:
                feedback += f"- {test}\n"

        if test_output and not specific_errors:
            feedback += f"\n**Test Output** (last 800 chars):\n```\n{test_output[-800:]}\n```\n"

        feedback += "\n**You MUST read the skill and evolution experiences to fix these failures.**\n"
        return feedback


def _parse_output(raw_output: str) -> dict | None:
    start_marker = "===JIUWENSWARM_OUTPUT_START==="
    end_marker = "===JIUWENSWARM_OUTPUT_END==="

    start_idx = raw_output.find(start_marker)
    end_idx = raw_output.find(end_marker)

    if start_idx == -1 or end_idx == -1 or start_idx >= end_idx:
        return None

    json_str = raw_output[start_idx + len(start_marker):end_idx].strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def _estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    total += len(item["text"]) // 4
    return total


def _get_runner_script() -> str:
    return '''
import sys
import os
import json
import traceback
import subprocess
import time
import uuid
import urllib.request
import urllib.error
import asyncio

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ.setdefault("EVOLUTION_AUTO_SCAN", "true")
os.environ.setdefault("EVOLUTION_AUTO_SAVE", "true")

_ACP_STDOUT = open(sys.stdout.fileno(), "w", closefd=False)

# Read timeout from environment variable, default to 800 seconds
_AGENT_TIMEOUT = int(os.environ.get("JIUWENSWARM_AGENT_TIMEOUT", "800"))

def _error_result(err_msg):
    return {"final_response": "", "messages": [], "failed": True, "partial": False, "error": err_msg}

_EVOLUTION_WAIT_SECONDS = int(os.environ.get("JIUWENSWARM_EVOLUTION_WAIT", "60"))

def _wait_for_ws_port(host, port, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            conn = urllib.request.urlopen(f"http://{host}:{port}/", timeout=2)
            conn.close()
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.5)
    return False

async def _run_agent_async():
    agent_proc = None
    gateway_proc = None
    final_response = ""
    try:
        agent_log = open("/tmp/jiuwenswarm_agent_server.log", "w")
        agent_proc = subprocess.Popen(
            [sys.executable, "-m", "jiuwenswarm.server.app_agentserver"],
            stdin=subprocess.DEVNULL,
            stdout=agent_log,
            stderr=subprocess.STDOUT,
        )
        agent_host = os.environ.get("AGENT_SERVER_HOST", "127.0.0.1")
        agent_port = int(os.environ.get("AGENT_SERVER_PORT", "18092"))
        if not _wait_for_ws_port(agent_host, agent_port, timeout=60):
            agent_log.close()
            err_detail = ""
            try:
                with open("/tmp/jiuwenswarm_agent_server.log", "r", errors="replace") as f:
                    err_detail = f.read()
            except Exception:
                pass
            return _error_result(f"AgentServer failed to start on port {agent_port}: {err_detail}")

        gateway_log = open("/tmp/jiuwenswarm_gateway.log", "w")
        gateway_proc = subprocess.Popen(
            [sys.executable, "-m", "jiuwenswarm.gateway.app_gateway"],
            stdin=subprocess.DEVNULL,
            stdout=gateway_log,
            stderr=subprocess.STDOUT,
        )
        gateway_host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
        gateway_port = int(os.environ.get("GATEWAY_PORT", "19000"))
        if not _wait_for_ws_port(gateway_host, gateway_port, timeout=60):
            gateway_log.close()
            err_detail = ""
            try:
                with open("/tmp/jiuwenswarm_gateway.log", "r", errors="replace") as f:
                    err_detail = f.read()
            except Exception:
                pass
            return _error_result(f"Gateway failed to start on port {gateway_port}: {err_detail}")

        with open("/tmp/jiuwenswarm_instruction.txt", "r", encoding="utf-8") as f:
            instruction = f.read().strip()

        if not instruction:
            return _error_result("Empty instruction")

        system_message = ""
        try:
            with open("/tmp/jiuwenswarm_system_message.txt", "r", encoding="utf-8") as f:
                system_message = f.read().strip()
        except FileNotFoundError:
            pass

        full_instruction = instruction
        if system_message:
            full_instruction = system_message + "\\n\\n---\\n\\n" + instruction

        try:
            from websockets.legacy.client import connect as ws_connect
        except ImportError:
            from websockets import connect as ws_connect

        ws_url = f"ws://{gateway_host}:{gateway_port}/ws"
        sys.stderr.write(f"[RUNNER] Connecting to WebChannel: {ws_url}\\n")
        sys.stderr.flush()

        async with ws_connect(ws_url, max_size=8 * 2**20) as ws:
            session_id = f"harbor_{uuid.uuid4().hex[:8]}"

            init_req_id = f"init_{uuid.uuid4().hex[:8]}"
            init_frame = {
                "type": "req",
                "id": init_req_id,
                "method": "initialize",
                "params": {"session_id": session_id},
            }
            await ws.send(json.dumps(init_frame, ensure_ascii=False))
            sys.stderr.write(f"[RUNNER] Sent initialize, session_id={session_id}\\n")
            sys.stderr.flush()

            init_resp = None
            t0 = time.time()
            while (time.time() - t0) < 30:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(raw)
                if data.get("type") == "res" and data.get("id") == init_req_id:
                    init_resp = data
                    break
                if data.get("type") == "event" and data.get("event") == "connection.ack":
                    break

            session_req_id = f"session_{uuid.uuid4().hex[:8]}"
            session_frame = {
                "type": "req",
                "id": session_req_id,
                "method": "session.create",
                "params": {"session_id": session_id},
            }
            await ws.send(json.dumps(session_frame, ensure_ascii=False))

            session_resp = None
            t0 = time.time()
            while (time.time() - t0) < 30:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(raw)
                if data.get("type") == "res" and data.get("id") == session_req_id:
                    session_resp = data
                    break
            if session_resp and not session_resp.get("ok"):
                err = session_resp.get("error", "unknown")
                return _error_result(f"session.create failed: {err}")

            chat_req_id = f"chat_{uuid.uuid4().hex[:8]}"
            chat_frame = {
                "type": "req",
                "id": chat_req_id,
                "method": "chat.send",
                "params": {
                    "session_id": session_id,
                    "content": full_instruction,
                    "mode": "agent.plan",
                },
            }
            await ws.send(json.dumps(chat_frame, ensure_ascii=False))
            sys.stderr.write(f"[RUNNER] Sent chat.send, content_len={len(full_instruction)}\\n")
            sys.stderr.flush()

            final_response = ""
            done = False
            t0 = time.time()
            last_log_time = t0

            messages = []
            current_assistant_msg = {"role": "assistant", "content": ""}
            current_tool_calls = []
            tool_results_buffer = {}
            evolution_events = []

            def _flush_current_round():
                nonlocal current_assistant_msg, current_tool_calls
                if current_assistant_msg.get("content") or current_tool_calls:
                    if current_tool_calls:
                        current_assistant_msg["tool_calls"] = current_tool_calls.copy()
                    messages.append(current_assistant_msg.copy())

                    for tool_call in current_tool_calls:
                        tool_id = tool_call.get("id", "")
                        tool_result = tool_results_buffer.get(tool_id, "")
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": tool_result
                        }
                        messages.append(tool_msg)

                current_assistant_msg = {"role": "assistant", "content": ""}
                current_tool_calls = []

            iteration_count = 0
            evolution_wait_start = None
            while (time.time() - t0) < _AGENT_TIMEOUT:
                iteration_count += 1

                if done and evolution_wait_start is None:
                    evolution_wait_start = time.time()
                    sys.stderr.write("[RUNNER] chat.final received, now waiting for evolution events...\\n")
                    sys.stderr.flush()

                if evolution_wait_start is not None:
                    evolution_elapsed = time.time() - evolution_wait_start
                    if evolution_elapsed >= _EVOLUTION_WAIT_SECONDS:
                        sys.stderr.write(
                            f"[RUNNER] Evolution wait timeout "
                            f"({_EVOLUTION_WAIT_SECONDS}s), stopping event loop\\n"
                        )
                        sys.stderr.flush()
                        break

                current_time = time.time()
                if current_time - last_log_time >= 10:
                    elapsed = current_time - t0
                    evo_info = (
                        f", evolution_wait={evolution_elapsed:.1f}s"
                        if evolution_wait_start else ""
                    )
                    sys.stderr.write(
                        f"[RUNNER] Still waiting... elapsed={elapsed:.1f}s, "
                        f"iterations={iteration_count}, "
                        f"response_len={len(final_response)}{evo_info}\\n"
                    )
                    sys.stderr.flush()
                    last_log_time = current_time

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    sys.stderr.write(f"[RUNNER] WebSocket recv error: {e}\\n")
                    sys.stderr.flush()
                    break

                if not raw:
                    continue

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                frame_type = data.get("type")

                if frame_type == "res":
                    req_id = data.get("id")
                    if req_id == chat_req_id:
                        if not data.get("ok"):
                            err = data.get("error", "unknown")
                            return _error_result(f"chat.send failed: {err}")
                    continue

                if frame_type == "event":
                    event_name = data.get("event", "")
                    payload = data.get("payload", {})

                    if event_name == "chat.delta":
                        if current_tool_calls and current_assistant_msg.get("content"):
                            _flush_current_round()
                        content = payload.get("content", "")
                        if content:
                            final_response += content
                            current_assistant_msg["content"] += content

                    elif event_name == "chat.tool_call":
                        tool_call_info = payload.get("tool_call", {})
                        tool_id = tool_call_info.get("tool_call_id", tool_call_info.get("id", ""))
                        if not tool_id:
                            tool_id = f"tool_{len(current_tool_calls)}"
                        tool_name = tool_call_info.get("name", "unknown")
                        tool_args = tool_call_info.get("arguments", {})

                        tool_call_entry = {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args) if isinstance(tool_args, dict) else str(tool_args)
                            }
                        }
                        current_tool_calls.append(tool_call_entry)

                    elif event_name == "chat.tool_result":
                        tool_id = payload.get("tool_call_id", "")
                        tool_result = payload.get("result", "")
                        if tool_id:
                            tool_results_buffer[tool_id] = str(tool_result)

                    elif event_name == "chat.final":
                        done = True

                    elif event_name == "evolution_status":
                        evolution_events.append({
                            "event": "evolution_status",
                            "status": payload.get("status"),
                            "skill_name": payload.get("skill_name"),
                            "request_id": payload.get("request_id"),
                        })

                    elif event_name == "ask_user_question":
                        evolution_events.append({
                            "event": "ask_user_question",
                            "request_id": payload.get("request_id"),
                            "is_evolution_approval": payload.get("is_evolution_approval", False),
                        })

        _flush_current_round()

        trajectory = [{"role": "user", "content": full_instruction}]
        trajectory.extend(messages)

        return {
            "final_response": final_response,
            "messages": trajectory,
            "evolution_events": evolution_events,
        }

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return _error_result(str(e))
    finally:
        if final_response and _EVOLUTION_WAIT_SECONDS > 0:
            sys.stderr.write(f"[RUNNER] Waiting {_EVOLUTION_WAIT_SECONDS}s for skill evolution...\\n")
            sys.stderr.flush()
            time.sleep(_EVOLUTION_WAIT_SECONDS)
            sys.stderr.write("[RUNNER] Evolution wait completed, shutting down...\\n")
            sys.stderr.flush()
        for p in [gateway_proc, agent_proc]:
            if p:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()

result = asyncio.run(_run_agent_async())
_ACP_STDOUT.write("===JIUWENSWARM_OUTPUT_START===\\n")
_ACP_STDOUT.write(json.dumps(result, ensure_ascii=False, default=str) + "\\n")
_ACP_STDOUT.write("===JIUWENSWARM_OUTPUT_END===\\n")
_ACP_STDOUT.flush()
'''
