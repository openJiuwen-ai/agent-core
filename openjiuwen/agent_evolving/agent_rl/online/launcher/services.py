# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process launch helpers and runtime resolution for the online RL loop."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.agent_evolving.agent_rl.online.launcher.schema import (
    GatewayServiceConfig,
    OnlineRLConfig,
    VLLMServiceConfig,
)
from openjiuwen.agent_evolving.agent_rl.online.launcher.workspace import build_trajectory_env_updates
from openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_config import resolve_default_ppo_config_path

log = logging.getLogger('online_rl')
DEFAULT_GATEWAY_APP_FACTORY = 'openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app'
EXISTING_SERVICE_HEALTH_TIMEOUT = 30.0


@dataclass(frozen=True)
class LaunchRuntime:
    inference_url: str
    judge_url: str
    gateway_base_url: str
    gateway_api_url: str
    lora_repo: str
    skip_vllm: bool
    skip_judge: bool
    reuse_inference_for_judge: bool
    judge_label: str
    ports_to_check: tuple[tuple[str, str, int], ...]


def resolve_launch_runtime(cfg: OnlineRLConfig, *, script_dir: Path) -> LaunchRuntime:
    """Resolve service URLs, skip flags, and port checks from config."""
    lora_repo = cfg.training.lora_repo or str(script_dir / 'lora_repo')
    skip_vllm = cfg.inference.existing_url is not None
    inference_url = cfg.inference.existing_url or f'http://{url_host(cfg.inference.host)}:{cfg.inference.port}'
    gateway_base_url = f'http://{url_host(cfg.gateway.host)}:{cfg.gateway.port}'
    gateway_api_url = f'{gateway_base_url}/v1'

    reuse_inference_for_judge = False
    if cfg.judge.existing_url:
        judge_url = cfg.judge.existing_url
        skip_judge = True
    elif cfg.judge.reuse_inference_if_same_model and cfg.judge.model_name == cfg.inference.model_name:
        judge_url = inference_url
        skip_judge = True
        reuse_inference_for_judge = True
    else:
        judge_url = f'http://{url_host(cfg.judge.host)}:{cfg.judge.port}'
        skip_judge = False

    judge_label = 'reuse inference' if judge_url == inference_url else cfg.judge.model_name

    ports_to_check: list[tuple[str, str, int]] = [('Gateway', cfg.gateway.host, cfg.gateway.port)]
    if not skip_vllm:
        ports_to_check.append(('vLLM-Inference', cfg.inference.host, cfg.inference.port))
    if not skip_judge:
        ports_to_check.append(('vLLM-Judge', cfg.judge.host, cfg.judge.port))
    if cfg.jiuwen.enabled:
        ports_to_check.extend([
            ('JiuwenClaw-AgentServer', cfg.jiuwen.app_host, cfg.jiuwen.agent_server_port),
            ('JiuwenClaw-WS', cfg.jiuwen.app_host, cfg.jiuwen.ws_port),
            ('JiuwenClaw-Web', cfg.jiuwen.web_host, cfg.jiuwen.web_port),
        ])

    return LaunchRuntime(
        inference_url=inference_url,
        judge_url=judge_url,
        gateway_base_url=gateway_base_url,
        gateway_api_url=gateway_api_url,
        lora_repo=lora_repo,
        skip_vllm=skip_vllm,
        skip_judge=skip_judge,
        reuse_inference_for_judge=reuse_inference_for_judge,
        judge_label=judge_label,
        ports_to_check=tuple(ports_to_check),
    )


def url_host(host: str) -> str:
    return '127.0.0.1' if host in {'0.0.0.0', '::'} else host


def spawn_process(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    log_path: Path | None = None,
) -> subprocess.Popen:
    """Spawn child process and stream stdout/stderr to a file (if provided)."""
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(log_path, 'a', encoding='utf-8')
        try:
            return subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
            )
        finally:
            f.close()
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=None, stderr=None)


def start_vllm_service(
    service_cfg: VLLMServiceConfig,
    *,
    step_label: str,
    service_name: str,
    enable_runtime_lora: bool,
    log_path: Path | None = None,
) -> subprocess.Popen:
    """Start a vLLM OpenAI API server from config."""
    env = os.environ.copy()
    env.update(service_cfg.env)
    env['CUDA_VISIBLE_DEVICES'] = service_cfg.gpu_ids
    if enable_runtime_lora:
        env.setdefault('VLLM_ALLOW_RUNTIME_LORA_UPDATING', '1')

    cmd = [
        sys.executable,
        '-m',
        'vllm.entrypoints.openai.api_server',
        '--model',
        service_cfg.model_path,
        '--served-model-name',
        service_cfg.model_name or service_cfg.model_path,
        '--port',
        str(service_cfg.port),
        '--host',
        service_cfg.host,
        '--tensor-parallel-size',
        str(service_cfg.tp),
    ]
    cmd.extend(service_cfg.extra_args)

    log.info(
        '[%s] Starting %s vLLM (TP=%d) on GPU [%s], host=%s, port=%d ...',
        step_label,
        service_name,
        service_cfg.tp,
        service_cfg.gpu_ids,
        service_cfg.host,
        service_cfg.port,
    )
    return spawn_process(cmd, env=env, log_path=log_path)


def start_gateway(
    *,
    inference_url: str,
    judge_url: str,
    judge_model: str,
    model_id: str,
    model_path: str,
    lora_repo_root: str,
    gateway_cfg: GatewayServiceConfig,
    agent_core_root: Path,
    log_path: Path | None = None,
) -> subprocess.Popen:
    """Start agent-core gateway."""
    env = os.environ.copy()
    env['LLM_URL'] = inference_url
    env['JUDGE_URL'] = judge_url
    env['JUDGE_MODEL'] = judge_model
    env['MODEL_ID'] = model_id
    env['MODEL_PATH'] = model_path
    env['GATEWAY_PORT'] = str(gateway_cfg.port)
    env['RECORD_DIR'] = gateway_cfg.record_dir
    env['REDIS_URL'] = gateway_cfg.redis_url
    if lora_repo_root:
        env['LORA_REPO_ROOT'] = lora_repo_root
    if gateway_cfg.disable_trajectory_collection:
        env['DISABLE_GATEWAY_TRAJECTORY_COLLECTION'] = 'true'
    env.update(gateway_cfg.env)

    cmd = [
        sys.executable,
        '-m',
        'uvicorn',
        DEFAULT_GATEWAY_APP_FACTORY,
        '--factory',
        '--host',
        gateway_cfg.host,
        '--port',
        str(gateway_cfg.port),
        '--log-level',
        gateway_cfg.log_level,
    ]
    log.info('[2/5] Starting Gateway on %s:%d ...', gateway_cfg.host, gateway_cfg.port)
    return spawn_process(
        cmd,
        cwd=str(agent_core_root),
        env=env,
        log_path=log_path,
    )


def start_online_training_scheduler(
    *,
    cfg: OnlineRLConfig,
    runtime: LaunchRuntime,
):
    """Start the OnlineTrainingScheduler that polls RedisTrajectoryStore."""
    from openjiuwen.agent_evolving.agent_rl.online.inference.notifier import InferenceNotifier
    from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import OnlineTrainingScheduler
    from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRARepository

    train_gpu_count = len([gpu for gpu in cfg.training.gpu_ids.split(',') if gpu.strip()]) or 1
    scheduler = OnlineTrainingScheduler(
        redis_url=cfg.gateway.redis_url,
        poll_interval=float(cfg.training.scan_interval),
        min_samples_for_training=cfg.training.threshold,
        base_model_path=cfg.inference.model_path,
        lora_repo=LoRARepository(runtime.lora_repo),
        notifier=InferenceNotifier(runtime.inference_url),
        nproc_per_node=train_gpu_count,
        training_gpu_ids=cfg.training.gpu_ids,
        ppo_config_path=cfg.training.ppo_config or str(resolve_default_ppo_config_path()),
    )
    scheduler.start()
    return scheduler


def start_jiuwenclaw(
    *,
    jiuwenclaw_repo: Path,
    workspace_root: Path,
    trajectory_gateway_url: str,
    model_path: str,
    trajectory_mode: str,
    trajectory_batch_size: int = 8,
    app_host: str = '127.0.0.1',
    ws_port: int = 19000,
    web_host: str = '127.0.0.1',
    web_port: int = 5173,
) -> tuple[subprocess.Popen, subprocess.Popen | None]:
    """Start JiuwenClaw app + web frontend (if dist exists)."""
    env = os.environ.copy()
    env['PYTHONPATH'] = f"{jiuwenclaw_repo}:{env.get('PYTHONPATH', '')}".rstrip(':')
    trajectory_tenant_id = os.getenv('RL_ONLINE_TENANT_ID', '').strip() or os.getenv(
        'WEB_USER_ID', 'local-web-user'
    ).strip() or 'local-web-user'
    env['WEB_USER_ID'] = trajectory_tenant_id
    env['CUSTOM_HEADERS'] = json.dumps(
        {'x-user-id': trajectory_tenant_id},
        ensure_ascii=True,
        separators=(',', ':'),
    )

    env.update(
        build_trajectory_env_updates(
            gateway_url=trajectory_gateway_url,
            model_path=model_path,
            trajectory_batch_size=trajectory_batch_size,
            trajectory_mode=trajectory_mode,
            trajectory_tenant_id=trajectory_tenant_id,
        )
    )
    env['WEB_HOST'] = app_host
    env['WEB_PORT'] = str(ws_port)

    cmd = [sys.executable, '-m', 'jiuwenclaw.app']
    log.info('[4/5] Starting JiuwenClaw app ...')
    log.info(
        '  JiuwenClaw env: gateway=%s, model=%s, batch_size=%d, mode=%s; ws=%s:%d',
        trajectory_gateway_url,
        model_path,
        trajectory_batch_size,
        trajectory_mode,
        app_host,
        ws_port,
    )
    app_proc = subprocess.Popen(
        cmd,
        cwd=str(jiuwenclaw_repo),
        env=env,
        stdout=None,
        stderr=None,
    )

    web_proc = None
    dist_dir = jiuwenclaw_repo / 'jiuwenclaw' / 'web' / 'dist'
    if not dist_dir.exists():
        dist_dir = workspace_root / 'web' / 'dist'
    if dist_dir.exists():
        web_cmd = [
            sys.executable,
            '-m',
            'jiuwenclaw.app_web',
            '--host',
            web_host,
            '--port',
            str(web_port),
            '--dist',
            str(dist_dir),
            '--proxy-target',
            f'http://{url_host(app_host)}:{ws_port}',
        ]
        log.info(
            '[5/5] Starting JiuwenClaw web frontend at http://%s:%d (dist=%s, ws_proxy=%s:%d) ...',
            web_host,
            web_port,
            dist_dir,
            url_host(app_host),
            ws_port,
        )
        web_proc = subprocess.Popen(
            web_cmd,
            cwd=str(jiuwenclaw_repo),
            env=env,
            stdout=None,
            stderr=None,
        )
    else:
        log.warning('[5/5] Web dist not found, skipping frontend. '
                    'Build it: cd jiuwenclaw/jiuwenclaw/web && npm install && npm run build')

    return app_proc, web_proc


def print_launch_summary(
    *,
    cfg: OnlineRLConfig,
    cfg_path: Path,
    runtime: LaunchRuntime,
    web_started: bool,
) -> None:
    """Log runtime summary after successful startup."""
    lines = [
        '=' * 60,
        '  JiuwenClaw online RL loop started (v2: per-turn + Judge)',
        '',
        f'  Config file:      {cfg_path}',
    ]
    if cfg.jiuwen.enabled:
        ws_display_host = url_host(cfg.jiuwen.app_host)
        if web_started:
            web_display_host = url_host(cfg.jiuwen.web_host)
            lines.append(f'  Web frontend:    http://{web_display_host}:{cfg.jiuwen.web_port}')
        lines.append(f'  JiuwenClaw WS:   ws://{ws_display_host}:{cfg.jiuwen.ws_port}/ws')
    else:
        lines.append('  JiuwenClaw:      skipped (jiuwen.enabled=false)')
    lines.extend([
        f'  vLLM Inference:  {runtime.inference_url}',
        f'  vLLM Judge:      {runtime.judge_url} ({runtime.judge_label})',
        f'  Gateway proxy:   {runtime.gateway_base_url}',
        f'  Redis store:     {cfg.gateway.redis_url}',
        f'  Trajectory mode: {cfg.trajectory.mode}',
        f'  Trajectory log:  {cfg.gateway.record_dir}/ (JSONL, per-turn)',
        f'  LoRA repo:       {runtime.lora_repo}',
        f'  Train threshold: {cfg.training.threshold} samples',
        f'  Collect batch:   {cfg.trajectory.batch_size}',
        f'  Scan interval:   {cfg.training.scan_interval}s',
        f'  Training mode:   PPO (Ray)',
        f'  Train GPUs:      [{cfg.training.gpu_ids}]',
        '',
    ])
    if cfg.jiuwen.enabled:
        ws_display_host = url_host(cfg.jiuwen.app_host)
        if web_started:
            web_display_host = url_host(cfg.jiuwen.web_host)
            lines.append(f'  Open http://{web_display_host}:{cfg.jiuwen.web_port} to start chatting,')
        else:
            lines.append(f'  Chat via WebSocket (ws://{ws_display_host}:{cfg.jiuwen.ws_port}/ws),')
    lines.extend([
        '  Each turn auto-records token_ids + logprobs,',
        '  next turn triggers delayed Judge scoring,',
        '  when pending trajectories reach threshold, PPO LoRA training auto-triggers.',
        '  Press Ctrl+C to stop all services.',
        '=' * 60,
    ])
    log.info('\n%s', '\n'.join(lines))
