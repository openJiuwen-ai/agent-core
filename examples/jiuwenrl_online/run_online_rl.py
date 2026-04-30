# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuwenClaw online RL loop launcher."""

from __future__ import annotations

import logging
import importlib.util
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = Path.home() / '.jiuwenclaw'
CONFIG_ENV = WORKSPACE / 'config' / '.env'


def _package_parent(package_name: str) -> Path:
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.origin is None:
        raise RuntimeError(f'{package_name} is not importable. Install it with pip install -e . or pip install *.whl.')
    return Path(spec.origin).resolve().parent.parent


AGENT_CORE_ROOT = _package_parent('openjiuwen')
JIUWENCLAW_REPO = _package_parent('jiuwenclaw')

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
