#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run the mobile GUI agent with workspace skills (SkillUseRail + multimodal read).

Compared to ``run_mobile_gui_agent.py``:
- Enables skill discovery via ``enable_skill_discovery=True``.
- Seeds ``examples/mobile_gui/skills/{scheduling,github-com}`` into ``<workspace>/skills/``.

Prerequisites match ``run_mobile_gui_agent.py``. Configure ``examples/mobile_gui/.env``
(see ``.env.example``); override the goal with ``MOBILE_TASK`` if needed.

Run from repository root::

    uv run python examples/mobile_gui/run_mobile_gui_agent_with_skills.py
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

from example_utils import (
    MOBILE_GUI_DIR,
    bootstrap_runtime,
    build_chat_model,
    configure_import_paths,
    default_task,
    device_serial,
    env_int,
    print_agent_result,
    print_run_banner,
    run_agent_with_runner,
)

configure_import_paths()

from openjiuwen.harness.subagents.mobile_gui_agent import create_mobile_gui_agent  # noqa: E402

_WORKSPACE_SKILL_SOURCES: tuple[tuple[str, Path], ...] = (
    ("scheduling", MOBILE_GUI_DIR / "skills" / "scheduling"),
    ("github-com", MOBILE_GUI_DIR / "skills" / "github-com"),
)


def seed_workspace_skills(workspace_root: str) -> None:
    skills_root = Path(workspace_root) / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    for dirname, src in _WORKSPACE_SKILL_SOURCES:
        if not src.is_dir():
            raise FileNotFoundError(f"Missing vendored skill directory: {src}")
        shutil.copytree(src, skills_root / dirname, dirs_exist_ok=True)


async def main() -> None:
    bootstrap_runtime()
    model = build_chat_model()
    task = default_task()
    serial = device_serial()
    print_run_banner(serial=serial, task=task)

    conversation_id = f"mobile_skills_{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="mobile-gui-skills-ws-") as workspace_root:
        seed_workspace_skills(workspace_root)
        agent = create_mobile_gui_agent(
            model=model,
            workspace=workspace_root,
            max_iterations=env_int("MAX_ITERATIONS", 30),
            language="en",
            enable_skill_discovery=True,
        )
        result = await run_agent_with_runner(
            agent,
            query=task,
            conversation_id=conversation_id,
        )

    print_agent_result(result)


if __name__ == "__main__":
    asyncio.run(main())
