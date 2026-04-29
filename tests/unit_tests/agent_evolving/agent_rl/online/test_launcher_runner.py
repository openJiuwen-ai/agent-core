from __future__ import annotations

import json
import logging
import signal
from pathlib import Path


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        return 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9


class _FakeScheduler:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


def test_run_online_rl_loop_signal_shutdown_returns_and_stops_children(monkeypatch, tmp_path: Path):
    from openjiuwen.agent_evolving.agent_rl.online.launcher.schema import (
        GatewayServiceConfig,
        JiuwenConfig,
        JudgeConfig,
        OnlineRLConfig,
        TrajectoryConfig,
        TrainingConfig,
        VLLMServiceConfig,
    )
    from openjiuwen.agent_evolving.agent_rl.online.launcher.runner import LauncherPaths, run_online_rl_loop

    inference_proc = _FakeProc(101)
    judge_proc = _FakeProc(102)
    gateway_proc = _FakeProc(103)
    claw_proc = _FakeProc(104)
    web_proc = _FakeProc(105)
    scheduler = _FakeScheduler()
    handlers: dict[signal.Signals, object] = {}

    def _capture_signal(sig, handler):
        handlers[sig] = handler

    def _start_vllm_service(service_cfg, **kwargs):
        del service_cfg, kwargs
        if inference_proc.terminate_calls == 0 and judge_proc.terminate_calls == 0 and gateway_proc.terminate_calls == 0:
            return inference_proc
        return judge_proc

    started_vllm = {"count": 0}

    def _start_vllm(service_cfg, **kwargs):
        del service_cfg, kwargs
        started_vllm["count"] += 1
        return inference_proc if started_vllm["count"] == 1 else judge_proc

    def _start_gateway(**kwargs):
        del kwargs
        return gateway_proc

    def _start_jiuwenclaw(**kwargs):
        del kwargs
        return claw_proc, web_proc

    def _sleep(seconds: float) -> None:
        if seconds == 5:
            handlers[signal.SIGINT](signal.SIGINT, None)

    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner.signal.signal',
        _capture_signal,
    )
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner._check_port_free',
        lambda host, port: None,
    )
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner._wait_for_health',
        lambda url, timeout=300.0: None,
    )
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner.start_online_training_scheduler',
        lambda cfg, runtime: scheduler,
    )
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner.start_vllm_service',
        _start_vllm,
    )
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner.start_gateway',
        _start_gateway,
    )
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner.start_jiuwenclaw',
        _start_jiuwenclaw,
    )
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner.ensure_workspace',
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.runner.time.sleep',
        _sleep,
    )

    cfg = OnlineRLConfig(
        demo=False,
        inference=VLLMServiceConfig(
            model_path='/tmp/model',
            model_name='model-a',
            host='127.0.0.1',
            port=18000,
            gpu_ids='0,1',
            tp=2,
            existing_url=None,
            health_timeout=1.0,
            env={},
            extra_args=[],
        ),
        judge=JudgeConfig(
            model_path='/tmp/judge',
            model_name='model-b',
            host='127.0.0.1',
            port=18001,
            gpu_ids='2,3',
            tp=2,
            existing_url=None,
            health_timeout=1.0,
            reuse_inference_if_same_model=False,
            env={},
            extra_args=[],
        ),
        gateway=GatewayServiceConfig(
            host='127.0.0.1',
            port=18080,
            redis_url='redis://127.0.0.1:6379/0',
            record_dir=str(tmp_path / 'records'),
            log_level='info',
            health_timeout=1.0,
            disable_trajectory_collection=True,
            env={},
        ),
        trajectory=TrajectoryConfig(batch_size=4, mode='feedback_level'),
        training=TrainingConfig(
            gpu_ids='4,5',
            threshold=4,
            scan_interval=30,
            ppo_config=None,
            lora_repo=None,
        ),
        jiuwen=JiuwenConfig(
            enabled=True,
            agent_server_port=18092,
            app_host='127.0.0.1',
            ws_port=19000,
            web_host='127.0.0.1',
            web_port=5173,
        ),
    )
    paths = LauncherPaths(
        agent_core_root=tmp_path,
        jiuwenclaw_repo=tmp_path / 'jiuwenclaw',
        workspace_root=tmp_path / 'workspace',
        workspace_env=tmp_path / 'workspace' / 'config' / '.env',
        script_dir=tmp_path,
    )

    run_online_rl_loop(cfg=cfg, cfg_path=tmp_path / 'cfg.yaml', paths=paths)

    assert scheduler.stop_calls == 1
    for proc in [inference_proc, judge_proc, gateway_proc, claw_proc, web_proc]:
        assert proc.terminate_calls == 1


def test_print_launch_summary_works_without_gateway_mode(tmp_path: Path, caplog):
    from openjiuwen.agent_evolving.agent_rl.online.launcher.schema import (
        GatewayServiceConfig,
        JiuwenConfig,
        JudgeConfig,
        OnlineRLConfig,
        TrajectoryConfig,
        TrainingConfig,
        VLLMServiceConfig,
    )
    from openjiuwen.agent_evolving.agent_rl.online.launcher.services import LaunchRuntime, print_launch_summary

    cfg = OnlineRLConfig(
        demo=False,
        inference=VLLMServiceConfig(
            model_path='/tmp/model',
            model_name='model-a',
            host='127.0.0.1',
            port=18000,
            gpu_ids='0,1',
            tp=2,
            existing_url=None,
            health_timeout=1.0,
            env={},
            extra_args=[],
        ),
        judge=JudgeConfig(
            model_path='/tmp/judge',
            model_name='model-b',
            host='127.0.0.1',
            port=18001,
            gpu_ids='2,3',
            tp=2,
            existing_url=None,
            health_timeout=1.0,
            reuse_inference_if_same_model=False,
            env={},
            extra_args=[],
        ),
        gateway=GatewayServiceConfig(
            host='127.0.0.1',
            port=18080,
            redis_url='redis://127.0.0.1:6379/0',
            record_dir=str(tmp_path / 'records'),
            log_level='info',
            health_timeout=1.0,
            disable_trajectory_collection=True,
            env={},
        ),
        trajectory=TrajectoryConfig(batch_size=4, mode='feedback_level'),
        training=TrainingConfig(
            gpu_ids='4,5',
            threshold=4,
            scan_interval=30,
            ppo_config=None,
            lora_repo=None,
        ),
        jiuwen=JiuwenConfig(
            enabled=True,
            agent_server_port=18092,
            app_host='127.0.0.1',
            ws_port=19000,
            web_host='127.0.0.1',
            web_port=5173,
        ),
    )
    runtime = LaunchRuntime(
        inference_url='http://127.0.0.1:18002',
        judge_url='http://127.0.0.1:18003',
        gateway_base_url='http://127.0.0.1:18080',
        gateway_api_url='http://127.0.0.1:18080/v1',
        lora_repo=str(tmp_path / 'lora_repo'),
        skip_vllm=False,
        skip_judge=False,
        reuse_inference_for_judge=False,
        judge_label='model-b',
        ports_to_check=(),
    )

    with caplog.at_level(logging.INFO, logger='online_rl'):
        print_launch_summary(
            cfg=cfg,
            cfg_path=tmp_path / 'cfg.yaml',
            runtime=runtime,
            web_started=True,
        )

    assert 'Gateway proxy' in caplog.text
    assert 'Trajectory mode: feedback_level' in caplog.text
    assert 'Gateway mode' not in caplog.text


def test_ensure_workspace_writes_web_user_headers(monkeypatch, tmp_path: Path):
    from openjiuwen.agent_evolving.agent_rl.online.launcher.workspace import ensure_workspace

    monkeypatch.setenv('WEB_USER_ID', 'alice')
    monkeypatch.delenv('RL_ONLINE_TENANT_ID', raising=False)
    config_env = tmp_path / 'config' / '.env'
    config_env.parent.mkdir(parents=True)
    config_env.write_text('', encoding='utf-8')

    ensure_workspace(
        config_env=config_env,
        gateway_url='http://127.0.0.1:18080/v1',
        model_name='model-a',
        model_path='/tmp/model',
        trajectory_mode='feedback_level',
        trajectory_gateway_url='http://127.0.0.1:18080',
        trajectory_batch_size=4,
    )

    values = dict(
        line.split('=', 1)
        for line in config_env.read_text(encoding='utf-8').splitlines()
        if '=' in line
    )
    assert values['WEB_USER_ID'] == '"alice"'
    assert values['RL_ONLINE_TENANT_ID'] == '"alice"'
    assert json.loads(values['CUSTOM_HEADERS'].strip("'")) == {'x-user-id': 'alice'}


def test_start_jiuwenclaw_passes_web_user_headers(monkeypatch, tmp_path: Path):
    from openjiuwen.agent_evolving.agent_rl.online.launcher.services import start_jiuwenclaw

    calls = []

    class _StartedProc:
        pid = 123

    def _fake_popen(cmd, **kwargs):
        calls.append({'cmd': cmd, **kwargs})
        return _StartedProc()

    monkeypatch.setenv('WEB_USER_ID', 'bob')
    monkeypatch.delenv('RL_ONLINE_TENANT_ID', raising=False)
    monkeypatch.setattr(
        'openjiuwen.agent_evolving.agent_rl.online.launcher.services.subprocess.Popen',
        _fake_popen,
    )

    app_proc, web_proc = start_jiuwenclaw(
        jiuwenclaw_repo=tmp_path / 'jiuwenclaw',
        workspace_root=tmp_path / 'workspace',
        trajectory_gateway_url='http://127.0.0.1:18080',
        model_path='/tmp/model',
        trajectory_mode='feedback_level',
        trajectory_batch_size=4,
        app_host='127.0.0.1',
        ws_port=19000,
        web_host='127.0.0.1',
        web_port=5173,
    )

    assert app_proc.pid == 123
    assert web_proc is None
    assert len(calls) == 1
    env = calls[0]['env']
    assert env['WEB_USER_ID'] == 'bob'
    assert env['RL_ONLINE_TENANT_ID'] == 'bob'
    assert json.loads(env['CUSTOM_HEADERS']) == {'x-user-id': 'bob'}
