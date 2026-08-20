from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
import types
from pathlib import Path

import pytest


def _install_dashscope_stub(monkeypatch):
    dashscope_mod = types.ModuleType("dashscope")
    dashscope_mod.AioMultiModalEmbedding = types.SimpleNamespace(call=None)
    dashscope_mod.MultiModalEmbedding = types.SimpleNamespace(call=None)
    api_entities_mod = types.ModuleType("dashscope.api_entities")
    response_mod = types.ModuleType("dashscope.api_entities.dashscope_response")
    common_mod = types.ModuleType("dashscope.common")
    constants_mod = types.ModuleType("dashscope.common.constants")

    class DashScopeAPIResponse:
        pass

    response_mod.DashScopeAPIResponse = DashScopeAPIResponse
    constants_mod.REQUEST_TIMEOUT_KEYWORD = "timeout"
    monkeypatch.setitem(sys.modules, "dashscope", dashscope_mod)
    monkeypatch.setitem(sys.modules, "dashscope.api_entities", api_entities_mod)
    monkeypatch.setitem(sys.modules, "dashscope.api_entities.dashscope_response", response_mod)
    monkeypatch.setitem(sys.modules, "dashscope.common", common_mod)
    monkeypatch.setitem(sys.modules, "dashscope.common.constants", constants_mod)


def test_build_sft_sample_normalizes_messages_and_text():
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import build_sft_sample

    sample = build_sft_sample(
        user_id="u1",
        session_id="s1",
        messages=[{"role": "user", "content": "hello"}],
        assistant_message={"role": "assistant", "content": "world"},
        source_raw_id="raw-1",
        scenario="multi_turn_supervisor",
        metadata={"hint": "keep"},
    )

    assert sample["protocol_version"] == "sft-sample-v1"
    assert sample["response_text"] == "world"
    assert sample["metadata"]["hint"] == "keep"


def test_normalize_assistant_message_reads_openai_choice_message():
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import normalize_assistant_message

    message = normalize_assistant_message(
        {"choices": [{"message": {"role": "assistant", "content": "choice content"}}]}
    )

    assert message["role"] == "assistant"
    assert message["content"] == "choice content"


def test_build_direct_supervisor_sft_samples_reads_openai_choice_response():
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import (
        build_direct_supervisor_sft_samples,
    )

    samples = build_direct_supervisor_sft_samples(
        {
            "raw_id": "raw-openai",
            "session_id": "sess-openai",
            "user_id": "u1",
            "steps": [
                {
                    "type": "llm",
                    "step_index": 0,
                    "messages": [{"role": "user", "content": "hello"}],
                    "response": {"choices": [{"message": {"role": "assistant", "content": "world"}}]},
                }
            ],
        },
        scenario="multi_turn_supervisor",
        default_user_id="u1",
        target_model_id="teacher",
        flush_reason="invoke_end",
    )

    assert len(samples) == 1
    assert samples[0]["assistant_message"]["content"] == "world"
    assert samples[0]["response_text"] == "world"


def test_build_direct_supervisor_sft_samples_keeps_tool_call_response():
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import (
        build_direct_supervisor_sft_samples,
    )

    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "bash", "arguments": "{\"command\":\"pytest\"}"},
    }
    samples = build_direct_supervisor_sft_samples(
        {
            "raw_id": "raw-tool",
            "session_id": "sess-tool",
            "user_id": "u1",
            "steps": [
                {
                    "type": "llm",
                    "step_index": 0,
                    "messages": [{"role": "user", "content": "run tests"}],
                    "response": {"role": "assistant", "content": "", "tool_calls": [tool_call]},
                }
            ],
        },
        scenario="multi_turn_supervisor",
        default_user_id="u1",
        target_model_id="teacher",
        flush_reason="invoke_end",
    )

    assert len(samples) == 1
    assert samples[0]["assistant_message"]["tool_calls"] == [tool_call]
    assert samples[0]["response_text"] == ""


def test_build_direct_supervisor_sft_samples_marks_direct_upload():
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import (
        build_direct_supervisor_sft_samples,
    )

    samples = build_direct_supervisor_sft_samples(
        {
            "raw_id": "raw-1",
            "session_id": "sess-1",
            "user_id": "u1",
            "steps": [
                {
                    "type": "llm",
                    "step_index": 0,
                    "messages": [{"role": "user", "content": "hello"}],
                    "response": {"role": "assistant", "content": "world"},
                }
            ],
        },
        scenario="multi_turn_supervisor",
        default_user_id="u1",
        target_model_id="teacher",
        flush_reason="invoke_end",
    )

    assert len(samples) == 1
    assert samples[0]["protocol_version"] == "sft-sample-v1"
    assert samples[0]["metadata"]["direct_supervisor_upload"] is True
    assert samples[0]["metadata"]["flush_reason"] == "invoke_end"


def test_task_rollout_backend_registry_preserves_compatible_aliases():
    from openjiuwen.agent_evolving.agent_rl.online.core.task_rollouter import get_task_rollout_backend

    assert type(get_task_rollout_backend("docker")).__name__ == "DockerTaskRolloutBackend"
    assert type(get_task_rollout_backend("local_program")).__name__ == "LocalProgramTaskRolloutBackend"


def test_local_program_rollout_uses_current_python_env(tmp_path, monkeypatch):
    from pathlib import Path

    from openjiuwen.agent_evolving.agent_rl.online.core.task_rollouter import (
        SFTTaskCase,
        SFTTaskRolloutConfig,
        build_task_rollout_local_program_spec,
    )

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    monkeypatch.setenv("SFT_DOCKER_CONDA_ENV", "wrong-env")

    spec = build_task_rollout_local_program_spec(
        SFTTaskCase(
            instance_id="local-task",
            docker_image="local/python-local-task:latest",
            task_prompt="prompt",
            local_program_path=str(task_dir),
        ),
        SFTTaskRolloutConfig(
            gateway_url="http://127.0.0.1:18080",
            supervisor_url="http://127.0.0.1:18002",
            rollout_command="python -c 'import sys; print(sys.executable)'",
            local_repo_work_root=str(tmp_path / "work"),
        ),
    )

    command_text = spec.command[-1]
    assert "conda activate wrong-env" not in command_text
    assert f"export PATH={Path(sys.executable).resolve().parent}:$PATH" in command_text
    assert spec.env["SFT_TASK_LIGHT_CONFIG"] == "1"

def test_docker_runtime_default_agent_core_root(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.backends.rollouter import docker_runtime

    monkeypatch.delenv("SFT_DOCKER_AGENT_CORE_HOST_PATH", raising=False)
    monkeypatch.delenv("SFT_DOCKER_USE_HOST_CONDA", raising=False)
    monkeypatch.setenv("SFT_DOCKER_USE_HOST_CONDA", "0")

    mounts, pythonpath, command_prefix = docker_runtime.docker_runtime_mounts()
    repo_root = next(parent for parent in docker_runtime.Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())

    assert mounts[:2] == ["-v", f"{repo_root}:{repo_root}:ro"]
    assert pythonpath.split(":")[0] == str(repo_root)
    assert command_prefix == ""


def test_sft_converter_builds_session_batch():
    from datetime import datetime

    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.converter import SFTRawTrajectoryConverter
    from openjiuwen.agent_evolving.trajectory.model import Trajectory
    from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID, TRAJECTORY_SOURCE
    from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
    from openjiuwen.extensions.observability import semconv

    trajectory = Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {
                                TRAJECTORY_ID: "traj-1",
                                SESSION_ID: "sess-1",
                                TRAJECTORY_SOURCE: "rl_online",
                            }
                        )
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "test"},
                            "spans": [
                                {
                                    "traceId": "trace-1",
                                    "spanId": "llm-1",
                                    "name": "llm.call",
                                    "attributes": attributes_from_map(
                                        {
                                            semconv.GEN_AI_REQUEST_MODEL: "m1",
                                            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
                                            f"{semconv.GEN_AI_PROMPT}.0.content": "hello",
                                            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
                                            f"{semconv.GEN_AI_COMPLETION}.0.content": "world",
                                        }
                                    ),
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    batch = SFTRawTrajectoryConverter(tenant_id="u1", scenario="multi_turn_supervisor").convert(
        trajectory,
        session_done=True,
        flush_reason="invoke_end",
        original_task="task",
    )

    assert batch.protocol_version == "sft-raw-v1"
    assert batch.session_done is True
    assert batch.steps[0].response_text == "world"
    assert batch.original_task == "task"
    datetime.fromisoformat(batch.created_at)


def test_token_in_token_out_forwarder_builds_record_from_model_call_context():
    from types import SimpleNamespace

    from openjiuwen.agent_evolving.agent_rl.online.core.interaction import TokenInTokenOutForwarder
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs

    ctx = AgentCallbackContext(
        agent=SimpleNamespace(config=SimpleNamespace(model_name="m1")),
        inputs=ModelCallInputs(
            messages=[{"role": "user", "content": "hello"}],
            response={
                "role": "assistant",
                "content": "world",
                "choices": [{"prompt_token_ids": [1, 2], "token_ids": [3], "logprobs": [-0.1]}],
            },
        ),
    )

    record = TokenInTokenOutForwarder().from_model_call_context(ctx)

    assert record.prompt_str == "user: hello"
    assert record.prompt_ids == [1, 2]
    assert record.llm_str == "world"
    assert record.llm_ids == [3]
    assert record.logprobs == [-0.1]
    assert record.model_id == "m1"


def test_sft_online_rail_flushes_session_on_explicit_close(monkeypatch):
    from types import SimpleNamespace

    _install_dashscope_stub(monkeypatch)
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail
    from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs

    class _Uploader:
        def __init__(self):
            self.batches = []

        async def enqueue(self, batch):
            self.batches.append(batch)

    class _Config:
        model_name = "student"
        llm_return_token_ids = False
        custom_headers = None

    async def _run():
        uploader = _Uploader()
        rail = SFTOnlineRail(
            session_id="",
            gateway_endpoint="http://gateway.local",
            tenant_id="u1",
                uploader=uploader,
                async_evolution=False,
                session_done_on_invoke_end=False,
                trajectory_span_processor=TrajectorySpanProcessor(),
            )
        agent = SimpleNamespace(config=_Config())

        first = InvokeInputs(query="q1", conversation_id="same-session")
        await rail.before_invoke(AgentCallbackContext(agent=agent, inputs=first))
        await rail.after_model_call(AgentCallbackContext(
            agent=agent,
            inputs=ModelCallInputs(
                messages=[{"role": "user", "content": "q1"}],
                response={"role": "assistant", "content": "a1", "choices": [{"token_ids": [11]}]},
            ),
        ))
        await rail.after_invoke(AgentCallbackContext(agent=agent, inputs=first))
        assert uploader.batches == []

        second = InvokeInputs(query="q2", conversation_id="same-session")
        close_ctx = AgentCallbackContext(agent=agent, inputs=second)
        close_ctx.extra["session_done"] = True
        await rail.before_invoke(close_ctx)
        await rail.after_model_call(AgentCallbackContext(
            agent=agent,
            inputs=ModelCallInputs(
                messages=[{"role": "user", "content": "q2"}],
                response={"role": "assistant", "content": "a2", "choices": [{"token_ids": [12]}]},
            ),
        ))
        await rail.after_invoke(close_ctx)

        assert len(uploader.batches) == 1
        batch = uploader.batches[0]
        assert batch.protocol_version == "sft-raw-v1"
        assert batch.flush_reason == "explicit_close"
        assert batch.original_task == "q1"
        assert [step.response_text for step in batch.steps] == ["a1", "a2"]

    asyncio.run(_run())


def test_sft_online_rail_reads_dataset_case_from_env(monkeypatch):
    from types import SimpleNamespace

    _install_dashscope_stub(monkeypatch)
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail
    from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs

    class _Uploader:
        def __init__(self):
            self.batches = []

        async def enqueue(self, batch):
            self.batches.append(batch)

    class _Config:
        model_name = "student"
        llm_return_token_ids = False
        custom_headers = None

    async def _run():
        monkeypatch.setenv("SFT_DOCKER_IMAGE", "swebench/example:latest")
        monkeypatch.setenv("SFT_INSTANCE_ID", "example-1")
        monkeypatch.setenv("SFT_TASK_PROMPT", "fix the failing test")
        uploader = _Uploader()
        rail = SFTOnlineRail(
            session_id="",
            gateway_endpoint="http://gateway.local",
            tenant_id="u1",
                uploader=uploader,
                async_evolution=False,
                session_done_on_invoke_end=True,
                trajectory_span_processor=TrajectorySpanProcessor(),
            )
        agent = SimpleNamespace(config=_Config())
        invoke = InvokeInputs(query="", conversation_id="same-session")
        await rail.before_invoke(AgentCallbackContext(agent=agent, inputs=invoke))
        await rail.after_model_call(
            AgentCallbackContext(
                agent=agent,
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "fix the failing test"}],
                    response={"role": "assistant", "content": "done", "choices": [{"token_ids": [12]}]},
                ),
            )
        )
        await rail.after_invoke(AgentCallbackContext(agent=agent, inputs=invoke))

        assert len(uploader.batches) == 1
        batch = uploader.batches[0]
        assert batch.original_task == "fix the failing test"
        assert batch.dataset_case["docker_image"] == "swebench/example:latest"
        assert batch.dataset_case["instance_id"] == "example-1"

    asyncio.run(_run())


def test_sft_online_rail_direct_sample_upload_mode(monkeypatch):
    from types import SimpleNamespace

    _install_dashscope_stub(monkeypatch)
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rail import SFTOnlineRail
    from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs

    class _Uploader:
        def __init__(self):
            self.items = []

        async def enqueue(self, item):
            self.items.append(item)

    class _Config:
        model_name = "teacher"
        llm_return_token_ids = False
        custom_headers = None

    async def _run():
        uploader = _Uploader()
        rail = SFTOnlineRail(
            session_id="",
            gateway_endpoint="http://gateway.local",
            tenant_id="u1",
            uploader=uploader,
                async_evolution=False,
                session_done_on_invoke_end=True,
                upload_mode="sample",
                trajectory_span_processor=TrajectorySpanProcessor(),
            )
        agent = SimpleNamespace(config=_Config())
        invoke = InvokeInputs(query="fix bug", conversation_id="same-session")
        await rail.before_invoke(AgentCallbackContext(agent=agent, inputs=invoke))
        await rail.after_model_call(
            AgentCallbackContext(
                agent=agent,
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "fix bug"}],
                    response={"role": "assistant", "content": "patch", "choices": [{"token_ids": [12]}]},
                ),
            )
        )
        await rail.after_invoke(AgentCallbackContext(agent=agent, inputs=invoke))

        assert len(uploader.items) == 1
        sample = uploader.items[0]
        assert isinstance(sample, dict)
        assert sample["protocol_version"] == "sft-sample-v1"
        assert sample["user_id"] == "u1"
        assert sample["response_text"] == "patch"
        assert sample["metadata"]["direct_supervisor_upload"] is True

    asyncio.run(_run())


def test_inmemory_sft_store_tracks_raw_and_samples():
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.store import InMemorySFTStore

    async def _run():
        store = InMemorySFTStore()
        await store.save_raw({"raw_id": "r1", "user_id": "u1"})
        await store.save_sample({"sample_id": "s1", "user_id": "u1"})
        assert await store.get_pending_raw_count("u1") == 1
        assert await store.get_pending_sample_count("u1") == 1
        raw = await store.fetch_raw_and_mark_processing("u1", 1)
        sample = await store.fetch_samples_and_mark_training("u1", 1)
        assert raw[0]["_store_status"] == "processing"
        assert sample[0]["_store_status"] == "training"
        await store.mark_raw_processed(["r1"])
        await store.mark_samples_trained(["s1"])
        stats = await store.stats()
        assert stats["processed_raw"] == 1
        assert stats["trained_samples"] == 1

    asyncio.run(_run())


def test_multi_turn_supervisor_rollouter_splits_llm_steps():
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rollouter import (
        MultiTurnSupervisorRollouter,
        SFTRolloutContext,
    )

    class _Supervisor:
        async def complete(self, **kwargs):
            return {"role": "assistant", "content": f"teacher:{kwargs['messages'][-1]['content']}"}

    async def _run():
        rollouter = MultiTurnSupervisorRollouter()
        samples = await rollouter.rollout(
            [
                {
                    "raw_id": "r1",
                    "user_id": "u1",
                    "session_id": "sess-1",
                    "model_id": "student",
                    "steps": [
                        {"type": "llm", "step_index": 0, "messages": [{"role": "user", "content": "q1"}]},
                        {"type": "tool", "step_index": 1, "tool_name": "search"},
                        {"type": "llm", "step_index": 2, "messages": [{"role": "user", "content": "q2"}]},
                    ],
                }
            ],
            SFTRolloutContext(supervisor=_Supervisor(), target_model_id="student"),
        )

        assert [sample["sample_id"] for sample in samples] == ["r1:0:supervisor", "r1:2:supervisor"]
        assert [sample["response_text"] for sample in samples] == ["teacher:q1", "teacher:q2"]

    asyncio.run(_run())


def test_end_to_end_image_rollouter_accepts_external_samples():
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.rollouter import (
        EndToEndImageRollouter,
        SFTRolloutContext,
    )

    class _Supervisor:
        async def rollout(self, **kwargs):
            return {
                "samples": [
                    {
                        "messages": [{"role": "user", "content": "case task"}],
                        "assistant_message": {"role": "assistant", "content": "done"},
                    }
                ]
            }

    async def _run():
        samples = await EndToEndImageRollouter().rollout(
            [{"raw_id": "r1", "user_id": "u1", "session_id": "case-session", "steps": []}],
            SFTRolloutContext(supervisor=_Supervisor(), target_model_id="student"),
        )

        assert len(samples) == 1
        assert samples[0]["protocol_version"] == "sft-sample-v1"
        assert samples[0]["scenario"] == "end_to_end_image"
        assert samples[0]["response_text"] == "done"

    asyncio.run(_run())


def test_docker_rollouter_uses_cpu_only_container(monkeypatch, tmp_path):
    from openjiuwen.agent_evolving.agent_rl.online.backends.rollouter.docker_runtime import (
        build_jiuwenclaw_docker_command,
    )
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft import rollouter as rollouter_module

    agent_core_root = "/data1/lll/workspace/openjiuwen/code-opt/agent-core"
    jiuwenclaw_root = "/data1/lll/workspace/openjiuwen/jiuwenclaw"
    conda_root = str(tmp_path / "missing-conda-root")

    monkeypatch.setenv("SFT_DOCKER_ROLLOUT_COMMAND", "python -m jiuwenswarm.app")
    monkeypatch.setenv("SFT_DOCKER_AGENT_CORE_HOST_PATH", agent_core_root)
    monkeypatch.setenv("SFT_DOCKER_JIUWENCLAW_HOST_PATH", jiuwenclaw_root)
    monkeypatch.setenv("SFT_DOCKER_CONDA_ROOT", conda_root)

    request = rollouter_module._docker_rollout_request_from_raw(
        {
            "raw_id": "r1",
            "original_task": "fix task",
            "dataset_case": {"docker_image": "swebench/example:latest"},
        }
    )

    assert request is not None
    cmd = build_jiuwenclaw_docker_command(request)
    assert "--gpus" not in cmd
    assert "-v" in cmd
    assert f"{agent_core_root}:{agent_core_root}:ro" in cmd
    assert f"{jiuwenclaw_root}:{jiuwenclaw_root}:ro" in cmd
    assert f"{conda_root}:{conda_root}:ro" in cmd
    assert f"PYTHONPATH={agent_core_root}:{jiuwenclaw_root}" in cmd
    assert f"source {conda_root}/etc/profile.d/conda.sh; conda activate openjiuwen-rl" in cmd[-1]
    assert "swebench/example:latest" in cmd


def test_docker_runtime_env_groups_sft_injection(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.backends.rollouter.docker_runtime import (
        SFT_JIUWENCLAW_DOTENV_KEYS,
        SFTJiuwenclawDockerRequest,
        build_jiuwenclaw_docker_env,
    )

    request = SFTJiuwenclawDockerRequest(
        image="swebench/example:latest",
        task_prompt="fix bug",
        instance_id="case-1",
        dataset_case={"repo": "demo"},
        gateway_url="http://gateway.local",
        supervisor_url="http://supervisor.local",
        supervisor_token="token",
        supervisor_model="teacher",
        tenant_id="u1",
        rollout_command="python -m jiuwenswarm.app",
    )
    env = build_jiuwenclaw_docker_env(
        request,
        dataset_case_json='{"repo":"demo"}',
        pythonpath="/repo/agent-core:/repo/jiuwenclaw",
        data_dir="/tmp/case-1",
    )

    assert env["TRAIN_BACKEND"] == "SFT"
    assert env["SFT_ONLINE_UPLOAD_MODE"] == "raw"
    assert env["TRAJECTORY_GATEWAY_URL"] == "http://gateway.local"
    assert env["API_BASE"] == "http://supervisor.local/v1"
    assert "SFT_TASK_PROMPT" in SFT_JIUWENCLAW_DOTENV_KEYS
    assert env["PYTHONPATH"] == "/repo/agent-core:/repo/jiuwenclaw"


def test_run_docker_command_spec_timeout_returns_bounded_result(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.abstract.rollouter import TaskRolloutCommandSpec
    from openjiuwen.agent_evolving.agent_rl.online.backends.rollouter.docker_runtime import (
        run_docker_command_spec,
    )

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args") or args[0],
            timeout=7,
            output="partial out",
            stderr="partial err",
        )

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.agent_rl.online.backends.rollouter.docker_runtime.subprocess.run",
        _raise_timeout,
    )

    async def _run():
        result = await run_docker_command_spec(
            TaskRolloutCommandSpec(
                name="case-1",
                command=["bash", "-lc", "sleep 10"],
                timeout_seconds=7,
                env={},
            )
        )
        assert result.exit_code == 124
        assert "partial out" in result.stdout_tail
        assert "partial err" in result.stderr_tail
        assert "timed out after 7s" in result.stderr_tail.lower()

    asyncio.run(_run())


def test_managed_training_process_falls_back_without_killpg(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.core import training_process

    class _Process:
        pid = 12345

        def __init__(self):
            self.sent = []

        def poll(self):
            return None

        def send_signal(self, sig):
            self.sent.append(sig)

    proc = _Process()
    monkeypatch.delattr(training_process.os, "killpg", raising=False)
    monkeypatch.delattr(training_process.os, "getpgid", raising=False)

    runner = training_process.ManagedTrainingProcess("sft")
    runner._send_signal(proc, signal.SIGTERM)

    assert proc.sent == [signal.SIGTERM]


def test_managed_training_process_force_kill_uses_sigterm_when_sigkill_missing(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.core import training_process

    class _Process:
        pid = 54321

        def __init__(self):
            self.sent = []

        def poll(self):
            return None

        def send_signal(self, sig):
            self.sent.append(sig)

    proc = _Process()
    monkeypatch.setattr(
        training_process,
        "signal",
        types.SimpleNamespace(SIGINT=signal.SIGINT, SIGTERM=signal.SIGTERM, Signals=type(signal.SIGINT)),
    )
    monkeypatch.delattr(training_process.os, "killpg", raising=False)
    monkeypatch.delattr(training_process.os, "getpgid", raising=False)
    monkeypatch.setattr(training_process, "release_accelerator_memory", lambda: None)

    runner = training_process.ManagedTrainingProcess("sft")
    runner._process = proc
    runner.force_kill()

    assert proc.sent == [signal.SIGTERM]


def test_task_rollouter_loads_markdown_cases_and_passes_image_env(tmp_path, monkeypatch):
    import openjiuwen.agent_evolving.agent_rl.online.core.task_rollouter as task_rollouter_module
    from openjiuwen.agent_evolving.agent_rl.online.core.task_rollouter import (
        SFTTaskRolloutConfig,
        build_task_rollout_docker_command,
        load_sft_task_cases,
    )

    cases_file = tmp_path / "cases.md"
    cases_file.write_text(
        "```\n"
        "swebench/sweb.eval.x86_64.<repo>_1776_<instance-id>:latest\n"
        "```\n"
        "- [ ] `swebench/sweb.eval.x86_64.django_1776_django-11790:latest`\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SFT_DOCKER_USE_HOST_CONDA", "0")
    monkeypatch.setenv("SFT_DOCKER_AGENT_CORE_HOST_PATH", "/repo/agent-core")
    monkeypatch.setenv("SFT_DOCKER_AGENT_CORE_CONTAINER_PATH", "/repo/agent-core")
    monkeypatch.setenv("SFT_DOCKER_JIUWENCLAW_HOST_PATH", "/repo/jiuwenclaw")
    monkeypatch.setenv("SFT_DOCKER_JIUWENCLAW_CONTAINER_PATH", "/repo/jiuwenclaw")
    monkeypatch.setattr(
        task_rollouter_module,
        "_resolve_swe_bench_case",
        lambda instance_id: {
            "repo": "django",
            "base_commit": "abc123",
            "problem_statement": "Fix unicode validation in username validators.",
            "fail_to_pass": ["auth_tests.test_validators.UsernameValidatorsTests.test_unicode_validator"],
            "pass_to_pass": ["auth_tests.test_validators.UsernameValidatorsTests.test_valid_username"],
        }
        if instance_id == "django__django-11790"
        else {},
    )

    cases = load_sft_task_cases(cases_file)
    assert len(cases) == 1
    assert cases[0].instance_id == "django__django-11790"
    assert cases[0].docker_image == "swebench/sweb.eval.x86_64.django_1776_django-11790:latest"
    assert "Fix unicode validation in username validators." in cases[0].task_prompt
    assert "Patch Output Format" in cases[0].task_prompt
    assert cases[0].problem_statement == "Fix unicode validation in username validators."

    cmd = build_task_rollout_docker_command(
        cases[0],
        SFTTaskRolloutConfig(
            gateway_url="http://172.17.0.5:18080",
            supervisor_url="http://172.17.0.5:18002",
            supervisor_model="teacher",
        ),
    )
    assert "--gpus" not in cmd
    assert "SFT_DOCKER_IMAGE=swebench/sweb.eval.x86_64.django_1776_django-11790:latest" in cmd
    assert "SFT_INSTANCE_ID=django__django-11790" in cmd
    assert any(item.startswith("SFT_DATASET_CASE_JSON=") for item in cmd)
    assert "TRAJECTORY_GATEWAY_URL=http://172.17.0.5:18080" in cmd
    assert "API_BASE=http://172.17.0.5:18002/v1" in cmd
    assert "USE_RL_ONLINE_RAIL=1" in cmd
    assert "TRAIN_BACKEND=SFT" in cmd
    assert "SFT_ONLINE_UPLOAD_MODE=raw" in cmd
    assert "install_rl_online_rail_extension.py" not in cmd[-1]
    assert '"method": "chat.send"' in cmd[-1]

    direct_cmd = build_task_rollout_docker_command(
        cases[0],
        SFTTaskRolloutConfig(
            gateway_url="http://172.17.0.5:18080",
            supervisor_url="http://172.17.0.5:18002",
            supervisor_model="teacher",
            sft_upload_mode="sample",
        ),
    )
    assert "SFT_ONLINE_UPLOAD_MODE=sample" in direct_cmd


def test_sft_executor_dry_run_writes_generic_dataset(tmp_path):
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.trainer import SFTTrainingExecutor

    executor = SFTTrainingExecutor(
        base_model_path="/models/Qwen3-4B-Instruct-2507",
        lora_repo=None,
        notifier=None,
        training_gpu_ids="4,5",
        target_model_id="teacher",
        trainer_command="",
        dry_run=True,
    )

    async def _run():
        dataset_path = await executor.train_batch(
            user_id="u1",
            samples=[{"sample_id": "s1", "messages": [{"role": "user", "content": "hello"}], "assistant_message": {"role": "assistant", "content": "world"}}],
            training_count=1,
            tmp_root=str(tmp_path),
        )
        payload = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
        assert payload["samples"][0]["sample_id"] == "s1"
        assert payload["samples"][0]["messages"][-1]["loss_mask"] == 1

    asyncio.run(_run())


def test_sft_executor_requires_trainer_command_when_not_dry_run(tmp_path):
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.trainer import SFTTrainingExecutor

    executor = SFTTrainingExecutor(
        base_model_path="/models/Qwen3-4B-Instruct-2507",
        lora_repo=None,
        notifier=None,
        training_gpu_ids="4,5",
        target_model_id="teacher",
        trainer_command="",
        dry_run=False,
    )

    async def _run():
        with pytest.raises(ValueError, match="trainer_command"):
            await executor.train_batch(
                user_id="u1",
                samples=[{"sample_id": "s1", "messages": [{"role": "user", "content": "hello"}], "assistant_message": {"role": "assistant", "content": "world"}}],
                training_count=1,
                tmp_root=str(tmp_path),
            )

    asyncio.run(_run())


def test_sft_executor_uses_configurable_visible_devices_env(monkeypatch):
    from openjiuwen.agent_evolving.agent_rl.online.backends.sft.trainer import SFTTrainingExecutor

    executor = SFTTrainingExecutor(
        base_model_path="/models/Qwen3-4B-Instruct-2507",
        lora_repo=None,
        notifier=None,
        training_gpu_ids="4,5",
        target_model_id="teacher",
        trainer_command="echo ok",
        dry_run=False,
    )

    captured = {}

    def _run(command, *, cwd, env, shell):
        captured["env"] = dict(env)
        return 0

    monkeypatch.setenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "ASCEND_VISIBLE_DEVICES")
    monkeypatch.setattr(executor._process_runner, "run", _run)

    executor._run_trainer_command(
        user_id="u1",
        dataset_path=Path("/tmp/dataset.json"),
        output_dir=Path("/tmp/output"),
        run_dir=Path("/tmp/run"),
    )

    assert captured["env"]["ASCEND_VISIBLE_DEVICES"] == "4,5"
    assert "CUDA_VISIBLE_DEVICES" not in captured["env"] or captured["env"]["CUDA_VISIBLE_DEVICES"] != "4,5"
