# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway CLI and uvicorn factory entrypoints."""

from __future__ import annotations

import argparse

from ..config import GatewayConfig
from .bootstrap import _build_config_from_env, build_app_from_config


def create_app():
    """Factory for ``uvicorn openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app --factory``."""
    config = _build_config_from_env()
    return build_app_from_config(config)


def main() -> None:
    """CLI entry-point."""
    parser = argparse.ArgumentParser(description="Online-RL Gateway")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--llm-url", default="http://127.0.0.1:18000")
    parser.add_argument("--judge-url", default="")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--record-dir", default="records")
    parser.add_argument("--lora-repo-root", default="")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    config = GatewayConfig(
        host=args.host,
        port=args.port,
        llm_url=args.llm_url,
        judge_url=args.judge_url or args.llm_url,
        model_id=args.model_id,
        judge_model=args.judge_model,
        record_dir=args.record_dir,
        lora_repo_root=args.lora_repo_root,
        log_level=args.log_level,
    )
    app = build_app_from_config(config)

    import uvicorn
    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level.lower())


if __name__ == "__main__":
    main()
