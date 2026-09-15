# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Example: offline skill_train sleep cycle over JiuwenSwarm traces.

Prerequisite (daytime): a JiuwenSwarm observation dir with ``traces-*.jsonl``
(for example ``%USERPROFILE%\\.jiuwenswarm\\.trace``). Sleep groups spans by
``session.id`` and harvests one SessionDigest per session.

Gate 通过后自动经 EvolutionStore 归档并写入新 skill 版本。
仅更新轨迹中检测到的 skill（skill_tool 等）；无 hint 的任务会被跳过，不再创建兜底 skill。

多轮会话：每个实质请求切成一段、一段一条 task；问候被丢弃，纠错 / 追问归到前一个请求
并进入 soft rubric。``--rubric-synthesis llm`` 可让 optimizer 模型把 follow-up 进一步合成为
可核查清单。

Usage:
  uv run python examples/agent_evolving/skill_sleep_example.py \\
    --trajectory-dir %USERPROFILE%\\.jiuwenswarm\\.trace \\
    --skills-base-dir ./skills \\
    --backend model --dry-run
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.skill_train.llm_client import ChatLLMClient
from openjiuwen.agent_evolving.skill_train.sleep import SleepConfig, run_sleep_cycle
from openjiuwen.agent_evolving.skill_train.sleep.backend import build_backend
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model import Model


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _load_dotenv() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _build_chat_client() -> ChatLLMClient:
    provider = _env("MODEL_PROVIDER", "OPTIMIZER_PROVIDER", "TARGET_PROVIDER", default="openai")
    api_key = _env("API_KEY", "OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY")
    api_base = _env("API_BASE", "OPENAI_COMPATIBLE_BASE_URL")
    model_name = _env("MODEL_NAME", "OPENAI_COMPATIBLE_MODEL", "OPTIMIZER_MODEL", default="GLM-5.2")
    missing = [
        label
        for label, value in (
            ("API_KEY / OPENAI_COMPATIBLE_API_KEY", api_key),
            ("API_BASE / OPENAI_COMPATIBLE_BASE_URL", api_base),
            ("MODEL_NAME / OPENAI_COMPATIBLE_MODEL", model_name),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing))
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
        ),
        model_config=ModelRequestConfig(model=model_name),
    )
    return ChatLLMClient(llm=model, model=model_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="skill_train sleep cycle (JiuwenSwarm traces)")
    parser.add_argument(
        "--trajectory-dir",
        required=True,
        help="JiuwenSwarm .trace dir (traces-*.jsonl)",
    )
    parser.add_argument(
        "--skills-base-dir",
        required=True,
        help="EvolutionStore skills root (gate 通过后写入新 skill 版本)",
    )
    parser.add_argument(
        "--skill-name",
        default="",
        help="可选：仅当轨迹检测到该 skill 且 store 无内容时，配合 --skill-init 提供初始 baseline",
    )
    parser.add_argument(
        "--skill-init",
        default="",
        help="可选：与 --skill-name 配套的初始 SKILL.md 路径",
    )
    parser.add_argument("--session-id", default=None, help="可选：只 harvest 该 session")
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--staging-root", default="")
    parser.add_argument("--backend", default="model", choices=["mock", "model"])
    parser.add_argument(
        "--rubric-synthesis",
        default="off",
        choices=["off", "llm"],
        help="off: 仅用 follow-up 启发式拼 rubric；llm: 额外调用 optimizer 模型合成可核查的 rubric 清单",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只跑 harvest/mine/consolidate，不写 staging、不落盘",
    )
    args = parser.parse_args()

    traj_dir = Path(args.trajectory_dir).expanduser()
    if not traj_dir.exists():
        raise SystemExit(f"trajectory dir not found: {traj_dir}")

    target_client = None
    optimizer_client = None
    if args.backend == "model":
        _load_dotenv()
        client = _build_chat_client()
        target_client = client
        optimizer_client = client

    store = EvolutionStore(args.skills_base_dir)
    cfg = SleepConfig(
        trajectory_store_dir=str(traj_dir),
        skills_base_dir=args.skills_base_dir,
        skill_name=args.skill_name,
        skill_init=args.skill_init,
        session_id=args.session_id,
        state_dir=args.state_dir or str(Path("./outputs/skill_sleep_state").resolve()),
        staging_root=args.staging_root or "",
        backend=args.backend,
        rubric_synthesis=args.rubric_synthesis,
        progress=True,
    )
    outcome = run_sleep_cycle(
        cfg,
        dry_run=args.dry_run,
        backend=build_backend(
            args.backend,
            target_client=target_client,
            optimizer_client=optimizer_client,
        ),
        target_client=target_client,
        optimizer_client=optimizer_client,
        evolution_store=store,
    )

    group_info = ""
    if outcome.report.skill_groups:
        group_info = (
            " groups=["
            + ", ".join(f"{g.skill_name}:{g.status}:accepted={g.accepted}" for g in outcome.report.skill_groups)
            + "]"
        )

    adopt_info = ""
    if outcome.adopted_skills:
        parts = [f"{item.skill_name}:{item.previous_version}->{item.new_version}" for item in outcome.adopted_skills]
        adopt_info = " adopted=[" + ", ".join(parts) + "]"

    print(
        f"night={outcome.night} accepted={outcome.report.accepted} "
        f"tasks={outcome.report.n_tasks} staging={outcome.staging_dir}"
        f"{group_info}{adopt_info}"
    )
    if outcome.report.notes:
        print("notes: " + "; ".join(outcome.report.notes))


if __name__ == "__main__":
    main()
