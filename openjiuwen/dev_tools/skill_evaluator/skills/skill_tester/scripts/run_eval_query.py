# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a skill agent with a given prompt and skill path.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--prompt", "-p",
        required=True,
        help="The query/prompt to send to the agent.",
    )
    parser.add_argument(
        "--skill-path", "-s",
        required=True,
        type=Path,
        help="Path to the skill directory or file to be tested.",
    )
    parser.add_argument(
        "--output-path", "-o",
        type=Path,
        required=True,
        help=(
            "Path to save the result.\n"
            "  - If a directory is given, a timestamped .txt file is created inside it.\n"
            "  - If a file path is given, results are written to that file.\n"
        ),
    )
    parser.add_argument(
        "--files-base-dir", "-f",
        type=Path,
        default=None,
        help="Base directory for user-provided files (default: script directory).",
    )
    parser.add_argument(
        "--max-iterations", "-m",
        type=int,
        default=None,
        help="Maximum agent iterations (overrides MAX_ITERATIONS env var, default: 40).",
    )
    return parser.parse_args()


def resolve_output_file(output_path: Path | None) -> Path:
    """Return a concrete file path to write results to."""
    tz_utc8 = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz=tz_utc8).strftime("%Y%m%d_%H%M%S")
    default_filename = f"skill_test_result_{timestamp}.txt"

    if output_path is None:
        return Path.cwd() / default_filename

    # Treat as a directory if: it already is one, has no file extension,
    # or the original string ends with a separator
    looks_like_dir = (
        output_path.is_dir()
        or not output_path.suffix          # no extension → intended as folder
        or str(output_path).endswith(("/", "\\"))
    )

    if looks_like_dir:
        output_path.mkdir(parents=True, exist_ok=True)   # ← create it first
        return output_path / default_filename

    # Treat as explicit file path — create parent dirs if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


async def main():
    load_dotenv()
    args = parse_args()

    # ── Resolve paths ────────────────────────────────────────────────────────
    skill_path = args.skill_path.expanduser().resolve()
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill path does not exist: {skill_path}")

    files_base_dir = (
        args.files_base_dir.expanduser().resolve()
        if args.files_base_dir
        else Path(os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent)))
    )

    output_file = resolve_output_file(
        args.output_path.expanduser().resolve() if args.output_path else None
    )

    max_iterations = args.max_iterations or int(os.getenv("MAX_ITERATIONS", "40"))

    # ── LLM config from env ──────────────────────────────────────────────────
    api_base = os.getenv("API_BASE", "")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "")
    model_provider = os.getenv("MODEL_PROVIDER", "")
    verify_ssl = os.getenv("LLM_SSL_VERIFY", "False")
    out_put_dir = str(output_file.parent)

    # ── Build agent ──────────────────────────────────────────────────────────
    agent = ReActAgent(card=AgentCard(name="skill_agent", description="Skill Agent"))

    system_prompt = (
        "You are an intelligent assistant.\n"
        f"All user-provided files are located at '{files_base_dir}'\n"
        f"Put all generated files into {out_put_dir} folder\n"
        "You may use tools when necessary.\n"
    )

    sysop_card = SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=None),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)

    cfg = (
        ReActAgentConfig()
        .configure_model_client(
            provider=model_provider,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            verify_ssl=verify_ssl,
        )
        .configure_prompt_template([{"role": "system", "content": system_prompt}])
        .configure_max_iterations(max_iterations)
        .configure_context_engine(
            max_context_message_num=None,
            default_window_round_num=None,
        )
    )
    cfg.sys_operation_id = sysop_card.id
    agent.configure(cfg)

    # ── Register tools ───────────────────────────────────────────────────────
    for operation, tool in [
        ("fs", "read_file"),
        ("code", "execute_code"),
        ("shell", "execute_cmd"),
        ("fs", "write_file"),
    ]:
        card = Runner.resource_mgr.get_sys_op_tool_cards(
            sys_operation_id=sysop_card.id,
            operation_name=operation,
            tool_name=tool,
        )
        agent.ability_manager.add(card)

    # ── Register skill ───────────────────────────────────────────────────────
    await agent.register_skill(str(skill_path))
    logger.info(f"Skill loaded from: {skill_path}")

    # ── Run ──────────────────────────────────────────────────────────────────
    logger.info(f"Running agent with prompt: {args.prompt!r}")
    tz_utc8 = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz=tz_utc8).strftime("%Y%m%d_%H%M%S")
    res = await Runner.run_agent(
        agent=agent,
        inputs={"query": args.prompt, "conversation_id": f"cli_run_{timestamp}"},
    )

    output_text = res.get("output", str(res))
    logger.info(output_text)

    # ── Save result ──────────────────────────────────────────────────────────
    output_file.write_text(output_text, encoding="utf-8")
    logger.info(f"Result saved to: {output_file}")
    logger.info(f"\n✅  Result saved to: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())