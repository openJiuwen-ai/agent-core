# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuwenClaw online RL loop launcher."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
AGENT_CORE_ROOT = (_SCRIPT_DIR / '..' / '..').resolve()
REPO_ROOT = AGENT_CORE_ROOT.parent
JIUWENCLAW_REPO = REPO_ROOT / 'jiuwenclaw'
WORKSPACE = Path.home() / '.jiuwenclaw'
CONFIG_ENV = WORKSPACE / 'config' / '.env'

for p in [str(JIUWENCLAW_REPO), str(AGENT_CORE_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from openjiuwen.agent_evolving.agent_rl.online.launcher.cli import build_arg_parser, build_cli_overrides  # noqa: E402
from openjiuwen.agent_evolving.agent_rl.online.launcher.loader import load_runtime_config  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    from openjiuwen.agent_evolving.agent_rl.online.launcher import (
        LauncherPaths,
        run_online_rl_loop,
    )

    cfg, cfg_path = load_runtime_config(
        config_path=args.config,
        cli_overrides=build_cli_overrides(args),
    )
    paths = LauncherPaths(
        agent_core_root=AGENT_CORE_ROOT,
        jiuwenclaw_repo=JIUWENCLAW_REPO,
        workspace_root=WORKSPACE,
        workspace_env=CONFIG_ENV,
        script_dir=_SCRIPT_DIR,
    )
    run_online_rl_loop(cfg=cfg, cfg_path=cfg_path, paths=paths)


if __name__ == '__main__':
    main()
