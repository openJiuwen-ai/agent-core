import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger

from .prompts import SystemPromptBuilder
from .runner_script import get_runner_script


@dataclass
class RunContext:
    """Context for running JiuWenSwarm agent"""
    container: Any
    instruction: str
    iteration: int
    has_skill: bool = False
    evolution_suggestions: str | None = None
    previous_result: Any = None
    evolution_files: dict[str, str] | None = None


class JiuWenSwarmAdapter:
    """Adapter for running JiuWenSwarm in container"""

    JIUWENSWARM_SKILL_DIR = "/root/.jiuwenswarm/agent/workspace/skills"
    JIUWENSWARM_CONFIG_DIR = "/root/.jiuwenswarm/config"

    def __init__(self, config):
        self.config = config
        self._resolved_skill_name: str = config.task_id
        self._all_skill_names: list[str] = [config.task_id]
        self._prompt_builder = SystemPromptBuilder(
            config, self._resolved_skill_name, self._all_skill_names
        )

    def set_resolved_skill_name(self, name: str) -> None:
        """Set the resolved skill name."""
        self._resolved_skill_name = name
        self._prompt_builder = SystemPromptBuilder(
            self.config, self._resolved_skill_name, self._all_skill_names
        )

    def set_all_skill_names(self, names: list[str]) -> None:
        """Set all skill names."""
        self._all_skill_names = names
        self._prompt_builder = SystemPromptBuilder(
            self.config, self._resolved_skill_name, self._all_skill_names
        )

    async def setup(self, container) -> bool:
        logger.info("Setting up JiuWenSwarm...")

        result = await container.exec_in_container(
            "/opt/venv/bin/python3 -c 'import jiuwenswarm; print(\"OK\")'",
            timeout=30
        )

        if "OK" not in result.get("stdout", ""):
            logger.warning("JiuWenSwarm not found in container (base image may be missing)")
            return False

        logger.info("  ✓ JiuWenSwarm verified")

        await container.exec_in_container(
            f"mkdir -p {self.JIUWENSWARM_CONFIG_DIR}",
            timeout=10
        )

        model_provider = "DashScope" if "dashscope" in (self.config.api_base or "") else "openai"
        env_content = f"""API_BASE={self.config.api_base or ''}
API_KEY={self.config.api_key or ''}
MODEL_NAME={self.config.model_name}
MODEL_PROVIDER={model_provider}

# Skill Evolution Configuration
EVOLUTION_AUTO_SCAN="true"
EVOLUTION_AUTO_SAVE="true"
"""
        env_path = Path("/tmp/jiuwenswarm_env")
        env_path.write_text(env_content, encoding="utf-8")
        await container.copy_to_container(env_path, f"{self.JIUWENSWARM_CONFIG_DIR}/.env")
        if env_path.exists():
            env_path.unlink()
        logger.info("  ✓ Created .env file with API and evolution configuration")

        config_yaml = f"""preferred_language: en

models:
  default:
    model_client_config:
      api_base: ${{API_BASE}}
      api_key: ${{API_KEY}}
      model_name: ${{MODEL_NAME:-{self.config.model_name}}}
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
    enabled: true
    auto_scan: true
    auto_save: true
    skill_base_dir: /root/.jiuwenswarm/agent/workspace/skills

memory:
  engine: none
"""
        config_path = Path("/tmp/jiuwenswarm_config.yaml")
        config_path.write_text(config_yaml, encoding="utf-8")
        await container.copy_to_container(config_path, f"{self.JIUWENSWARM_CONFIG_DIR}/config.yaml")
        if config_path.exists():
            config_path.unlink()
        logger.info("  ✓ Created config.yaml with evolution enabled, auto_scan=true, auto_save=true")

        return True

    async def load_skill(
        self,
        container,
        skill_content: str | None,
        evolutions_content: str | None = None,
        evolution_files: dict[str, str] | None = None,
        skill_name: str | None = None
    ) -> bool:
        if not skill_content:
            logger.info("No skill to load")
            return True

        resolved_name = skill_name or self.config.task_id
        skill_dir = f"{self.JIUWENSWARM_SKILL_DIR}/{resolved_name}"

        await container.exec_in_container(f"mkdir -p {skill_dir}", timeout=10)

        skill_path = Path("/tmp/skill_to_load.md")
        skill_path.write_text(skill_content, encoding="utf-8")

        success = await container.copy_to_container(skill_path, f"{skill_dir}/SKILL.md")

        if success:
            logger.info("Skill loaded to container: %s/SKILL.md", skill_dir)
        else:
            logger.error("Failed to load skill to container")

        if skill_path.exists():
            skill_path.unlink()

        if evolutions_content:
            evo_path = Path("/tmp/evolutions_to_load.json")
            evo_path.write_text(evolutions_content, encoding="utf-8")
            evo_success = await container.copy_to_container(evo_path, f"{skill_dir}/evolutions.json")
            if evo_success:
                logger.info("Evolutions loaded to container: %s/evolutions.json (%d chars)", 
                        skill_dir, len(evolutions_content))
            else:
                logger.error("Failed to load evolutions to container")
            if evo_path.exists():
                evo_path.unlink()

        if evolution_files:
            evolution_dir = f"{skill_dir}/evolution"
            await container.exec_in_container(f"mkdir -p {evolution_dir}", timeout=10)

            for filename, file_content in evolution_files.items():
                file_path = Path(f"/tmp/evolution_{filename}")
                file_path.write_text(file_content, encoding="utf-8")
                file_success = await container.copy_to_container(file_path, f"{evolution_dir}/{filename}")
                if file_success:
                    logger.info("Evolution file loaded: %s/%s (%d chars)", evolution_dir, filename, len(file_content))
                else:
                    logger.error("Failed to load evolution file: %s", filename)
                if file_path.exists():
                    file_path.unlink()

        return success

    async def load_all_skills(
        self,
        container,
        all_skills: dict[str, str],
        all_evolutions: dict[str, str] | None = None,
        all_evolution_files: dict[str, dict[str, str]] | None = None,
    ) -> int:
        all_evolutions = all_evolutions or {}
        all_evolution_files = all_evolution_files or {}
        loaded = 0

        for skill_name, skill_content in all_skills.items():
            evo_content = all_evolutions.get(skill_name)
            evo_files = all_evolution_files.get(skill_name)
            ok = await self.load_skill(
                container,
                skill_content,
                evolutions_content=evo_content,
                evolution_files=evo_files,
                skill_name=skill_name,
            )
            if ok:
                loaded += 1

        logger.info("  Loaded %d/%d skills into container", loaded, len(all_skills))
        return loaded

    async def run(
        self,
        context: RunContext
    ) -> dict[str, Any]:
        """Run JiuWenSwarm agent with the provided context."""
        container = context.container
        instruction = context.instruction
        iteration = context.iteration
        has_skill = context.has_skill
        evolution_suggestions = context.evolution_suggestions
        previous_result = context.previous_result
        evolution_files = context.evolution_files

        logger.info("Running JiuWenSwarm (iteration %d)...", iteration)
        if has_skill:
            logger.info("  Using existing skill from previous iteration")
            if evolution_suggestions:
                logger.info("  Evolution suggestions provided")
            logger.info("  Agent will read SKILL.md and evolution files via system prompt guidance")
        else:
            logger.info("  No existing skill, will create new skill")

        instruction_path = Path("/tmp/instruction.txt")
        instruction_path.write_text(instruction, encoding="utf-8")

        await container.copy_to_container(instruction_path, "/tmp/jiuwenswarm_instruction.txt")

        system_message = self._prompt_builder.build(
            iteration, has_skill, evolution_suggestions, previous_result, evolution_files
        )
        if system_message:
            system_path = Path("/tmp/system_message.txt")
            system_path.write_text(system_message, encoding="utf-8")
            await container.copy_to_container(system_path, "/tmp/jiuwenswarm_system_message.txt")
            if system_path.exists():
                system_path.unlink()

        runner_script = get_runner_script()
        runner_path = Path("/tmp/jiuwenswarm_runner.py")
        runner_path.write_text(runner_script, encoding="utf-8")

        await container.copy_to_container(runner_path, "/tmp/jiuwenswarm_runner.py")

        if runner_path.exists():
            runner_path.unlink()
        if instruction_path.exists():
            instruction_path.unlink()

        start_time = time.time()
        evolution_wait = self.config.evolution_wait_time
        result = await container.exec_in_container(
            f"JIUWENSWARM_EVOLUTION_WAIT={evolution_wait} /opt/venv/bin/python3 /tmp/jiuwenswarm_runner.py",
            timeout=self.config.agent_timeout + evolution_wait + 30
        )
        execution_time = time.time() - start_time

        raw_output = result.get("stdout", "")
        stderr = result.get("stderr", "")
        returncode = result.get("returncode", -1)

        debug_dir = Path("/tmp/jiuwenswarm_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "raw_output.txt").write_text(raw_output, encoding="utf-8")
        if stderr:
            (debug_dir / "stderr.txt").write_text(stderr, encoding="utf-8")

        trajectory = []
        final_response = ""
        tokens_used = 0
        evolution_events = []

        parsed = self._parse_output(raw_output)
        if parsed:
            trajectory = parsed.get("messages", [])
            final_response = parsed.get("final_response", "")
            evolution_events = parsed.get("evolution_events", [])
            tokens_used = self._estimate_tokens(trajectory)
            logger.info("  Parsed trajectory: %d messages", len(trajectory))
            if evolution_events:
                logger.info("  Evolution events: %d", len(evolution_events))
                for evt in evolution_events:
                    if evt.get("event") == "evolution_status":
                        logger.info("    - Evolution %s: skill=%s request_id=%s", 
                                    evt.get("status"), evt.get("skill_name"), evt.get("request_id"))
                    elif evt.get("event") == "ask_user_question":
                        logger.info("    - Approval request: request_id=%s is_evolution=%s", 
                                    evt.get("request_id"), evt.get("is_evolution_approval"))
            else:
                logger.info("  No evolution events captured")
        else:
            logger.warning("Failed to parse JiuWenSwarm output")
            logger.info("  Raw output length: %d chars", len(raw_output))
            logger.info("  Stderr length: %d chars", len(stderr))
            logger.info("  Return code: %d", returncode)

            if returncode != 0:
                agent_log = await container.exec_in_container(
                    "cat /tmp/jiuwenswarm_agent_server.log 2>/dev/null | tail -100",
                    timeout=10
                )
                gateway_log = await container.exec_in_container(
                    "cat /tmp/jiuwenswarm_gateway.log 2>/dev/null | tail -100",
                    timeout=10
                )
                if agent_log.get("stdout"):
                    (debug_dir / "agent_server.log").write_text(agent_log["stdout"], encoding="utf-8")
                    logger.info("  Agent server log saved to %s/agent_server.log", debug_dir)
                if gateway_log.get("stdout"):
                    (debug_dir / "gateway.log").write_text(gateway_log["stdout"], encoding="utf-8")
                    logger.info("  Gateway log saved to %s/gateway.log", debug_dir)

        return {
            "raw_output": raw_output,
            "trajectory": trajectory,
            "final_response": final_response,
            "execution_time": execution_time,
            "tokens_used": tokens_used,
            "stderr": stderr,
            "returncode": returncode,
            "evolution_events": evolution_events
        }

    async def capture_created_skills(
        self,
        container
    ) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, str]]]:
        logger.info("Capturing created skills...")

        evolution_contents: dict[str, str] = {}
        evo_result = await container.exec_in_container(
            f"find {self.JIUWENSWARM_SKILL_DIR} -name 'evolutions.json' 2>/dev/null",
            timeout=30
        )

        if evo_result.get("returncode") == 0 and evo_result.get("stdout", "").strip():
            evo_files = [f.strip() for f in evo_result["stdout"].strip().split("\n") if f.strip()]
            logger.info("  Found %d evolutions.json files", len(evo_files))
            for evo_file in evo_files:
                skill_name = Path(evo_file).parent.name
                read_result = await container.exec_in_container(f"cat {evo_file}", timeout=30)
                if read_result.get("returncode") == 0:
                    evo_content = read_result["stdout"]
                    evolution_contents[skill_name] = evo_content
                    logger.info("  %s/evolutions.json: %d chars", skill_name, len(evo_content))

        evolution_files: dict[str, dict[str, str]] = {}
        evo_dir_result = await container.exec_in_container(
            f"find {self.JIUWENSWARM_SKILL_DIR} -path '*/evolution/*.md' 2>/dev/null",
            timeout=30
        )

        if evo_dir_result.get("returncode") == 0 and evo_dir_result.get("stdout", "").strip():
            evo_md_files = [f.strip() for f in evo_dir_result["stdout"].strip().split("\n") if f.strip()]
            logger.info("  Found %d evolution/*.md files", len(evo_md_files))
            for md_file in evo_md_files:
                skill_name = Path(md_file).parent.parent.name
                filename = Path(md_file).name
                read_result = await container.exec_in_container(f"cat {md_file}", timeout=30)
                if read_result.get("returncode") == 0:
                    if skill_name not in evolution_files:
                        evolution_files[skill_name] = {}
                    evolution_files[skill_name][filename] = read_result["stdout"]
                    logger.info("  %s/evolution/%s: %d chars", skill_name, filename, len(read_result["stdout"]))

        result = await container.exec_in_container(
            f"find {self.JIUWENSWARM_SKILL_DIR} -name 'SKILL.md' 2>/dev/null",
            timeout=30
        )

        if result.get("returncode", -1) != 0 or not result.get("stdout", "").strip():
            return {}, evolution_contents, evolution_files

        skill_files = [
            f.strip() for f in result["stdout"].strip().split("\n")
            if f.strip()
        ]

        created_skills = {}
        for skill_file in skill_files:
            skill_name = Path(skill_file).parent.name

            read_result = await container.exec_in_container(
                f"cat {skill_file}",
                timeout=30
            )

            if read_result.get("returncode") == 0:
                created_skills[skill_name] = read_result["stdout"]

        total_evo_files = sum(len(files) for files in evolution_files.values())
        logger.info("Captured %d skills, %d evolutions, %d evolution files", 
                    len(created_skills), len(evolution_contents), total_evo_files)
        return created_skills, evolution_contents, evolution_files

    @staticmethod
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

    @staticmethod
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
