# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Orchestration runtime for the online RL launcher."""

from __future__ import annotations

import logging
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.agent_evolving.agent_rl.config.online_config import OnlineRLConfig
from openjiuwen.agent_evolving.agent_rl.online.launcher.services import (
    EXISTING_SERVICE_HEALTH_TIMEOUT,
    print_launch_summary,
    resolve_launch_runtime,
    start_gateway,
    start_jiuwenclaw,
    start_online_training_scheduler,
    start_vllm_service,
    url_host,
)
from openjiuwen.agent_evolving.agent_rl.online.launcher.workspace import ensure_workspace

log = logging.getLogger('online_rl')


class _ShutdownRequested(Exception):
    """Internal signal used to stop the launcher loop cleanly."""


@dataclass(frozen=True)
class LauncherPaths:
    agent_core_root: Path
    jiuwenclaw_repo: Path
    workspace_root: Path
    workspace_env: Path
    script_dir: Path


def _check_port_free(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        if sock.connect_ex((url_host(host), port)) == 0:
            raise RuntimeError(
                f'Port {host}:{port} is already in use. '
                f'Kill the occupying process first: lsof -i :{port}'
            )


def _wait_for_health(url: str, timeout: float = 300.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError) as exc:
            log.debug('Health check retry url=%s err=%s', url, exc)
        time.sleep(2.0)
    raise TimeoutError(f'Health check {url} did not pass within {timeout}s')


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _check_required_ports(ports_to_check: tuple[tuple[str, str, int], ...]) -> None:
    for name, host, port in ports_to_check:
        _check_port_free(host, port)
        log.info('  Port %d (%s, host=%s) is free', port, name, host)


def _wait_for_service_healths(
    cfg: OnlineRLConfig,
    *,
    inference_url: str,
    judge_url: str,
    skip_vllm: bool,
    skip_judge: bool,
) -> None:
    if not skip_vllm:
        log.info('  Waiting for Inference vLLM health check (may take 1-3 min) ...')
        _wait_for_health(f'{inference_url}/health', timeout=cfg.inference.health_timeout)
        log.info('  Inference vLLM ready at %s', inference_url)
    else:
        _wait_for_health(f'{inference_url}/health', timeout=EXISTING_SERVICE_HEALTH_TIMEOUT)

    if not skip_judge:
        log.info('  Waiting for Judge vLLM health check (may take 2-5 min) ...')
        _wait_for_health(f'{judge_url}/health', timeout=cfg.judge.health_timeout)
        log.info('  Judge vLLM ready at %s', judge_url)
    else:
        _wait_for_health(f'{judge_url}/health', timeout=EXISTING_SERVICE_HEALTH_TIMEOUT)


def run_online_rl_loop(
    *,
    cfg: OnlineRLConfig,
    cfg_path: Path,
    paths: LauncherPaths,
) -> None:
    """Run the full online RL service orchestration loop."""
    if cfg.demo:
        log.info('Demo mode enabled (compatibility flag): using configured runtime options.')

    log_dir = paths.script_dir / 'logs'
    log_dir.mkdir(exist_ok=True)
    runtime = resolve_launch_runtime(cfg, script_dir=paths.script_dir)
    _check_required_ports(runtime.ports_to_check)
    if runtime.reuse_inference_for_judge:
        log.info('Judge will reuse inference vLLM (%s)', cfg.inference.model_name)

    vllm_proc = None
    judge_proc = None
    gateway_proc = None
    claw_proc = None
    web_proc = None
    training_scheduler = None
    shutdown_started = False

    def _shutdown(signum=None, frame=None):
        nonlocal shutdown_started
        del signum, frame
        if shutdown_started:
            return
        shutdown_started = True
        log.info('Shutting down all services ...')
        if training_scheduler:
            training_scheduler.stop()
        _terminate(web_proc)
        _terminate(claw_proc)
        _terminate(gateway_proc)
        _terminate(judge_proc)
        _terminate(vllm_proc)
        log.info('All services stopped.')

    def _handle_signal(signum, frame):
        _shutdown(signum, frame)
        raise _ShutdownRequested()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        if not runtime.skip_vllm:
            vllm_proc = start_vllm_service(
                cfg.inference,
                step_label='1a/5',
                service_name='Inference',
                enable_runtime_lora=True,
                log_path=log_dir / 'inference_vllm.log',
            )
        else:
            log.info('[1a/5] Using existing inference at %s', runtime.inference_url)

        if not runtime.skip_judge:
            judge_proc = start_vllm_service(
                cfg.judge,
                step_label='1b/5',
                service_name='Judge',
                enable_runtime_lora=False,
                log_path=log_dir / 'judge_vllm.log',
            )
        else:
            log.info('[1b/5] Using existing Judge at %s', runtime.judge_url)

        _wait_for_service_healths(
            cfg,
            inference_url=runtime.inference_url,
            judge_url=runtime.judge_url,
            skip_vllm=runtime.skip_vllm,
            skip_judge=runtime.skip_judge,
        )

        gateway_proc = start_gateway(
            inference_url=runtime.inference_url,
            judge_url=runtime.judge_url,
            judge_model=cfg.judge.model_name,
            model_id=cfg.inference.model_name,
            model_path=cfg.inference.model_path,
            lora_repo_root=runtime.lora_repo,
            gateway_cfg=cfg.gateway,
            agent_core_root=paths.agent_core_root,
            log_path=log_dir / 'gateway.log',
        )
        _wait_for_health(f'{runtime.gateway_base_url}/health', timeout=cfg.gateway.health_timeout)
        log.info('  Gateway ready at %s', runtime.gateway_base_url)

        log.info(
            '[3/5] Starting OnlineTrainingScheduler (PPO, threshold=%d, interval=%ds) ...',
            cfg.training.threshold,
            cfg.training.scan_interval,
        )
        training_scheduler = start_online_training_scheduler(cfg=cfg, runtime=runtime)
        log.info('  OnlineTrainingScheduler running (PPO, train GPU: [%s])', cfg.training.gpu_ids)

        if not cfg.jiuwen.enabled:
            log.info('[4/5] Skip JiuwenClaw startup (jiuwen.enabled=false)')
        else:
            ensure_workspace(
                config_env=paths.workspace_env,
                gateway_url=runtime.gateway_api_url,
                model_name=cfg.inference.model_name,
                model_path=cfg.inference.model_path,
                trajectory_mode=cfg.trajectory.mode,
                trajectory_gateway_url=runtime.gateway_base_url,
                trajectory_batch_size=cfg.trajectory.batch_size,
            )
            claw_proc, web_proc = start_jiuwenclaw(
                jiuwenclaw_repo=paths.jiuwenclaw_repo,
                workspace_root=paths.workspace_root,
                trajectory_gateway_url=runtime.gateway_base_url,
                model_path=cfg.inference.model_path,
                trajectory_mode=cfg.trajectory.mode,
                trajectory_batch_size=cfg.trajectory.batch_size,
                app_host=cfg.jiuwen.app_host,
                ws_port=cfg.jiuwen.ws_port,
                web_host=cfg.jiuwen.web_host,
                web_port=cfg.jiuwen.web_port,
            )
            time.sleep(5)
            log.info('  JiuwenClaw app started (pid=%d)', claw_proc.pid)
            if web_proc:
                log.info('  JiuwenClaw web started (pid=%d)', web_proc.pid)

        print_launch_summary(
            cfg=cfg,
            cfg_path=cfg_path,
            runtime=runtime,
            web_started=web_proc is not None,
        )

        while True:
            for name, proc in [
                ('vllm', vllm_proc),
                ('judge_vllm', judge_proc),
                ('gateway', gateway_proc),
                ('jiuwenclaw', claw_proc),
            ]:
                if proc is not None and proc.poll() is not None:
                    log.error('%s exited unexpectedly with code %d — stopping', name, proc.returncode)
                    return

            time.sleep(30)

    except _ShutdownRequested:
        log.info('Launcher shutdown requested.')
    except KeyboardInterrupt:
        log.info('Launcher interrupted by keyboard.')
    except Exception:
        log.exception('Fatal error')
    finally:
        _shutdown()
