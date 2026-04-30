# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CLI parsing helpers for online RL launcher runtime config."""

from __future__ import annotations

import argparse
from typing import cast

from .loader import DEFAULT_CONFIG_FILENAME


def _set_nested_value(data: dict[str, object], path: str, value: object) -> None:
    current = data
    parts = path.split('.')
    for part in parts[:-1]:
        current = cast(dict[str, object], current.setdefault(part, {}))
    current[parts[-1]] = value


def build_cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    cli_mappings = {
        'demo': 'demo',
        'model_path': 'inference.model_path',
        'model_name': 'inference.model_name',
        'vllm_gpu': 'inference.gpu_ids',
        'vllm_tp': 'inference.tp',
        'vllm_port': 'inference.port',
        'inference_url': 'inference.existing_url',
        'judge_model_path': 'judge.model_path',
        'judge_model_name': 'judge.model_name',
        'judge_gpu': 'judge.gpu_ids',
        'judge_tp': 'judge.tp',
        'judge_port': 'judge.port',
        'judge_url': 'judge.existing_url',
        'gateway_port': 'gateway.port',
        'redis_url': 'gateway.redis_url',
        'threshold': 'training.threshold',
        'scan_interval': 'training.scan_interval',
        'train_gpu': 'training.gpu_ids',
        'ppo_config': 'training.ppo_config',
        'trajectory_batch_size': 'trajectory.batch_size',
        'lora_repo': 'training.lora_repo',
        'jiuwen_agent_server_port': 'jiuwen.agent_server_port',
        'jiuwen_ws_port': 'jiuwen.ws_port',
        'jiuwen_web_host': 'jiuwen.web_host',
        'jiuwen_web_port': 'jiuwen.web_port',
    }
    for arg_name, cfg_path in cli_mappings.items():
        value = getattr(args, arg_name)
        if value is not None:
            _set_nested_value(overrides, cfg_path, value)
    if args.skip_jiuwen:
        _set_nested_value(overrides, 'jiuwen.enabled', False)
    return overrides


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='JiuwenClaw online RL loop: interact -> trajectory collect -> PPO train -> LoRA hot-load',
    )
    parser.add_argument(
        '--config',
        default=None,
        help=(
            f'YAML overlay on built-in defaults from {DEFAULT_CONFIG_FILENAME} '
            '(optional)'
        ),
    )
    parser.add_argument('--model-path', default=None, help='Base model path')
    parser.add_argument('--model-name', default=None, help='Model name registered in vLLM')
    parser.add_argument('--vllm-gpu', default=None, help='GPUs for vLLM inference (comma-separated)')
    parser.add_argument('--vllm-tp', type=int, default=None, help='Inference tensor parallel size')
    parser.add_argument('--vllm-port', type=int, default=None, help='vLLM inference port')

    parser.add_argument('--judge-model-path', default=None, help='Judge model path')
    parser.add_argument('--judge-model-name', default=None, help='Judge model name registered in vLLM')
    parser.add_argument('--judge-gpu', default=None, help='GPUs for Judge vLLM (comma-separated)')
    parser.add_argument('--judge-tp', type=int, default=None, help='Judge tensor parallel size')
    parser.add_argument('--judge-port', type=int, default=None, help='Judge vLLM port')

    parser.add_argument('--gateway-port', type=int, default=None, help='Gateway port')
    parser.add_argument('--redis-url', default=None, help='RedisTrajectoryStore URL')
    parser.add_argument('--threshold', type=int, default=None, help='Sample count threshold to trigger training')
    parser.add_argument('--scan-interval', type=int, default=None, help='TrainingScheduler poll interval (seconds)')
    parser.add_argument('--train-gpu', default=None, help='GPUs for training (comma-separated)')
    parser.add_argument(
        '--ppo-config',
        default=None,
        help=(
            'Custom Hydra PPO YAML (default: compose verl ``ppo_trainer`` + '
            'online_config.ONLINE_PPO_VERL_HYDRA_OVERLAY)'
        ),
    )
    parser.add_argument(
        '--trajectory-batch-size',
        type=int,
        default=None,
        help='Trajectory env batch size for JiuwenClaw (e.g. TRAJECTORY_BATCH_SIZE)',
    )
    parser.add_argument('--lora-repo', default=None, help='LoRA storage dir (default: ./lora_repo)')
    parser.add_argument('--jiuwen-agent-server-port', type=int, default=None, help='JiuwenClaw agent server port')
    parser.add_argument(
        '--demo',
        action='store_true',
        default=None,
        help='Compat flag for legacy launch scripts; marks demo mode, does not change behavior',
    )
    parser.add_argument(
        '--inference-url',
        default=None,
        help='Skip inference vLLM launch, connect to existing service',
    )
    parser.add_argument(
        '--judge-url',
        default=None,
        help='Skip Judge vLLM launch, connect to existing Judge service',
    )
    parser.add_argument(
        '--skip-jiuwen',
        '--skip_jiuwen',
        dest='skip_jiuwen',
        action='store_true',
        default=False,
        help='Skip JiuwenClaw app/web launch (use when already running externally)',
    )
    parser.add_argument('--jiuwen-ws-port', type=int, default=None, help='JiuwenClaw WebSocket port')
    parser.add_argument('--jiuwen-web-host', default=None, help='JiuwenClaw web frontend listen address')
    parser.add_argument('--jiuwen-web-port', type=int, default=None, help='JiuwenClaw web frontend port')
    return parser
