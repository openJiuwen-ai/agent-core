from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import pytest

from tests.system_tests.agent_evolving.agent_rl.online.system_harness import OnlineRLSystem


@pytest.fixture
def online_rl_system(tmp_path):
    with OnlineRLSystem(tmp_path) as system:
        yield system


def _publish_terminal_samples(system: OnlineRLSystem, *, session_id: str, count: int) -> None:
    task = system.start_task(session_id=session_id, reward_mode="terminal").json()
    for index in range(count):
        response = system.complete(
            messages=[{"role": "user", "content": f"sample {index}"}],
            session_id=session_id,
        )
        assert response.status_code == 200
    assert system.stop_task(task["rl_task_id"]).status_code == 200
    assert system.reward(task["rl_task_id"], 1.0).json() == {"sample_count": count}


def _wait_json(
    fetch: Callable[[], Any],
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 10,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = fetch().json()
        if predicate(body):
            return body
        time.sleep(0.05)
    raise AssertionError(f"condition was not reached; last response: {body}")


def _openai_sse_events(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]


def test_terminal_task_captures_real_gateway_completion(online_rl_system: OnlineRLSystem) -> None:
    system = online_rl_system

    bypass = system.complete(messages=[{"role": "user", "content": "before RL"}])
    assert bypass.status_code == 200
    assert bypass.json()["choices"][0]["message"]["content"] == "fake completion"

    assert system.start_service().json()["status"] == "running"
    task = system.start_task(session_id="session-terminal", reward_mode="terminal").json()
    completion = system.complete(
        messages=[{"role": "user", "content": "capture this"}],
        session_id="session-terminal",
    )

    assert completion.status_code == 200
    body = completion.json()
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
    assert "prompt_token_ids" not in body
    assert "token_ids" not in body["choices"][0]

    assert system.stop_task(task["rl_task_id"]).json()["status"] == "finalized"
    assert system.reward(task["rl_task_id"], 0.75).json() == {"sample_count": 1}
    assert system.reward(task["rl_task_id"], 0.75).json() == {"sample_count": 1}
    conflict = system.reward(task["rl_task_id"], 0.5)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "reward_conflict"
    trajectories = system.trajectories().json()
    assert trajectories["items"][0]["policy_version"] == "base"
    trajectory = system.trajectory(trajectories["items"][0]["trajectory_id"]).json()
    assert trajectory["judge"]["score"] == 0.75

    assert system.stop_service().json()["status"] == "stopped"
    assert system.complete(messages=[{"role": "user", "content": "after RL"}]).status_code == 200
    assert system.lora().status_code == 200


def test_hmac_and_control_errors_use_public_contract(online_rl_system: OnlineRLSystem) -> None:
    system = online_rl_system

    assert system.unsigned_request("POST", "/v1/rl/service/start").status_code == 401
    unavailable = system.start_task(session_id="session-offline", reward_mode="terminal")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "rl_service_unavailable"

    system.start_service()
    invalid = system.start_task(session_id="session-invalid", reward_mode="unknown")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_reward_mode"


def test_delayed_feedback_across_openai_and_anthropic_streams(
    online_rl_system: OnlineRLSystem,
) -> None:
    system = online_rl_system
    system.start_service()
    task = system.start_task(session_id="session-delayed", reward_mode="delayed_feedback").json()

    openai_stream = system.complete(
        messages=[{"role": "user", "content": "first"}],
        session_id="session-delayed",
        turn_id="turn-1",
        stream=True,
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
    )
    assert openai_stream.status_code == 200
    assert "data: [DONE]" in openai_stream.text
    assert "prompt_token_ids" not in openai_stream.text
    assert "token_ids" not in openai_stream.text
    openai_events = _openai_sse_events(openai_stream.text)
    assert openai_events[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "lookup"
    assert openai_events[0]["choices"][0]["finish_reason"] == "tool_calls"
    assert openai_events[1]["usage"] == {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}

    anthropic = system.anthropic(
        messages=[{"role": "user", "content": "second"}],
        session_id="session-delayed",
        turn_id="turn-2",
        tools=[{"name": "lookup", "input_schema": {"type": "object"}}],
    )
    assert anthropic.status_code == 200
    assert anthropic.json()["stop_reason"] == "tool_use"
    assert anthropic.json()["content"][0]["type"] == "tool_use"
    assert anthropic.json()["usage"] == {"input_tokens": 2, "output_tokens": 1}

    assert system.stop_task(task["rl_task_id"]).json()["status"] == "finalized"
    summaries = system.trajectories().json()["items"]
    details = [system.trajectory(item["trajectory_id"]).json() for item in summaries]
    assert {detail["judge"]["tag"] for detail in details} == {"feedback", "session_done"}
    assert all(detail["judge"]["score"] == 0.625 for detail in details)

    anthropic_stream = system.anthropic(
        messages=[{"role": "user", "content": "ordinary stream"}],
        stream=True,
        tools=[{"name": "lookup", "input_schema": {"type": "object"}}],
    )
    assert anthropic_stream.status_code == 200
    assert "event: message_start" in anthropic_stream.text
    assert "event: message_stop" in anthropic_stream.text
    assert '"type":"tool_use"' in anthropic_stream.text
    assert '"stop_reason":"tool_use"' in anthropic_stream.text
    assert '"input_tokens":2' in anthropic_stream.text
    assert '"output_tokens":1' in anthropic_stream.text
    for private_field in ("prompt_token_ids", "token_ids", "logprobs", "total_tokens"):
        assert private_field not in anthropic_stream.text


def test_delayed_feedback_concurrency_late_turn_and_judge_failure(
    online_rl_system: OnlineRLSystem,
) -> None:
    system = online_rl_system
    system.start_service()
    task = system.start_task(session_id="session-concurrent", reward_mode="delayed_feedback").json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                system.complete,
                messages=[{"role": "user", "content": f"parallel {index}"}],
                session_id="session-concurrent",
                turn_id="turn-1",
            )
            for index in range(2)
        ]
        assert [future.result().status_code for future in futures] == [200, 200]

    next_turn = system.complete(
        messages=[{"role": "user", "content": "feedback"}],
        session_id="session-concurrent",
        turn_id="turn-2",
    )
    assert next_turn.status_code == 200
    assert len(system.trajectories().json()["items"]) == 2

    late = system.complete(
        messages=[{"role": "user", "content": "late"}],
        session_id="session-concurrent",
        turn_id="turn-1",
    )
    assert late.status_code == 409
    assert (
        _wait_json(lambda: system.task(task["rl_task_id"]), lambda body: body["status"] == "aborted")["finish_reason"]
        == "capture_failed"
    )

    failed_task = system.start_task(session_id="session-judge-fail", reward_mode="delayed_feedback").json()
    assert (
        system.complete(
            messages=[{"role": "user", "content": "judge me"}],
            session_id="session-judge-fail",
            turn_id="turn-1",
        ).status_code
        == 200
    )
    system.set_fault("judge", "fail")
    failed = system.complete(
        messages=[{"role": "user", "content": "feedback"}],
        session_id="session-judge-fail",
        turn_id="turn-2",
    )
    assert failed.status_code == 502
    assert (
        _wait_json(
            lambda: system.task(failed_task["rl_task_id"]),
            lambda body: body["status"] == "aborted",
        )["finish_reason"]
        == "capture_failed"
    )
    assert len(system.trajectories().json()["items"]) == 2


def test_training_activates_lora_and_preserves_pinned_task(
    online_rl_system: OnlineRLSystem,
) -> None:
    system = online_rl_system
    system.start_service()
    insufficient = system.start_training()
    assert insufficient.status_code == 409
    assert insufficient.json()["error"]["code"] == "insufficient_samples"

    pinned = system.start_task(session_id="session-pinned-base", reward_mode="terminal").json()
    training_task = system.start_task(session_id="session-training", reward_mode="terminal").json()
    system.complete(messages=[{"role": "user", "content": "sample 1"}], session_id="session-training")
    system.complete(messages=[{"role": "user", "content": "sample 2"}], session_id="session-training")
    system.stop_task(training_task["rl_task_id"])
    system.reward(training_task["rl_task_id"], 1.0)

    started = system.start_training()
    assert started.status_code == 201
    run = system.wait_training(started.json()["training_run_id"])
    assert run["status"] == "succeeded"
    assert run["sample_count"] == 2
    assert run["policy_versions"] == {"base": 2}
    assert system.lora().json()["active_lora"]["lora_name"] == "model-1:v1"
    assert [path for path, _ in system.lora_control_calls].count("/v1/load_lora_adapter") == 2

    active = system.start_task(session_id="session-active-v1", reward_mode="terminal").json()
    assert active["policy_lora_name"] == "model-1:v1"
    assert system.delete_lora().status_code == 204
    assert system.lora().json()["status"] == "base"

    system.complete(messages=[{"role": "user", "content": "still v1"}], session_id="session-active-v1")
    system.complete(messages=[{"role": "user", "content": "still base"}], session_id="session-pinned-base")
    assert system.completion_models[-2:] == ["model-1:v1", "model-1"]
    system.stop_task(active["rl_task_id"])
    system.stop_task(pinned["rl_task_id"])
    assert [path for path, _ in system.lora_control_calls].count("/v1/unload_lora_adapter") == 2


def test_upstream_and_redis_failures_remain_bounded(online_rl_system: OnlineRLSystem) -> None:
    system = online_rl_system
    for status in (400, 429, 500):
        system.set_upstream_status(status)
        openai = system.complete(messages=[{"role": "user", "content": "fail"}])
        assert openai.status_code == status
        assert openai.json()["error"]["message"] == f"fake upstream {status}"
    system.set_upstream_status(200)

    system.start_service()
    if not system.owns_redis:
        pytest.skip("Redis pause fault injection requires the harness-owned container")
    system.pause_redis()
    try:
        unavailable = system.start_task(session_id="session-redis-down", reward_mode="terminal")
        assert unavailable.status_code == 500
        assert unavailable.json()["error"]["code"] == "internal_error"
    finally:
        system.resume_redis()
    assert system.start_task(session_id="session-redis-restored", reward_mode="terminal").status_code == 201


def test_disconnect_hook_timeouts_and_drain_timeout_are_bounded(
    online_rl_system: OnlineRLSystem,
) -> None:
    system = online_rl_system
    system.start_service()

    disconnected = system.start_task(session_id="session-disconnect", reward_mode="terminal").json()
    system.set_completion_delay(1)
    system.disconnect_completion(session_id="session-disconnect")
    time.sleep(0.5)
    disconnected_stop = system.stop_task(disconnected["rl_task_id"])
    assert disconnected_stop.status_code == 200, disconnected_stop.text
    assert disconnected_stop.json()["status"] == "finalized"
    assert system.trajectories().json()["items"] == []
    system.set_completion_delay(0)

    before_task = system.start_task(session_id="session-before-timeout", reward_mode="terminal").json()
    system.set_fault("before", "delay")
    before_timeout = system.complete(
        messages=[{"role": "user", "content": "before timeout"}],
        session_id="session-before-timeout",
    )
    assert before_timeout.status_code == 504
    system.set_fault("before", "success")
    assert (
        _wait_json(
            lambda: system.task(before_task["rl_task_id"]),
            lambda body: body["status"] == "aborted",
        )["finish_reason"]
        == "capture_failed"
    )

    after_task = system.start_task(session_id="session-after-timeout", reward_mode="terminal").json()
    system.set_fault("after", "delay")
    assert (
        system.complete(
            messages=[{"role": "user", "content": "after timeout"}],
            session_id="session-after-timeout",
        ).status_code
        == 200
    )
    assert (
        _wait_json(
            lambda: system.task(after_task["rl_task_id"]),
            lambda body: body["status"] == "aborted",
        )["finish_reason"]
        == "capture_failed"
    )
    system.set_fault("after", "success")

    draining = system.start_task(session_id="session-drain-timeout", reward_mode="terminal").json()
    system.set_completion_delay(1)
    completion_result: list[Any] = []
    completion = threading.Thread(
        target=lambda: completion_result.append(
            system.complete(
                messages=[{"role": "user", "content": "drain timeout"}],
                session_id="session-drain-timeout",
            )
        )
    )
    completion.start()
    system.wait_for_upstream_request()
    timed_out = system.stop_task(draining["rl_task_id"])
    assert timed_out.status_code == 504
    assert timed_out.json()["error"]["code"] == "task_drain_timeout"
    completion.join(timeout=5)
    assert not completion.is_alive()
    system.set_completion_delay(0)
    assert (
        _wait_json(
            lambda: system.task(draining["rl_task_id"]),
            lambda body: body["status"] == "aborted",
        )["finish_reason"]
        == "timeout"
    )


def test_training_failure_stop_and_service_restart_restore_fixed_batch(
    online_rl_system: OnlineRLSystem,
) -> None:
    system = online_rl_system
    system.start_service()
    _publish_terminal_samples(system, session_id="session-recovery", count=2)

    system.set_ppo_mode("fail")
    failed_start = system.start_training().json()
    failed = system.wait_training(failed_start["training_run_id"])
    assert failed["status"] == "failed"
    assert "fake PPO failed" in failed["failure_reason"]

    system.set_ppo_mode("block")
    canceled_start = system.start_training().json()
    canceled = system.stop_training(canceled_start["training_run_id"]).json()
    assert canceled["status"] == "canceled"

    restarted = system.start_training().json()
    system.crash_service()
    failure_record = system.wait_service("failed")
    assert failure_record["failure_reason"]
    assert system.complete(messages=[{"role": "user", "content": "gateway survives"}]).status_code == 200
    assert system.lora().status_code == 200

    system.set_ppo_mode("success")
    assert system.start_service().json()["status"] == "running"
    recovered = system.training(restarted["training_run_id"]).json()
    assert recovered["status"] == "failed"
    assert recovered["failure_reason"] == "service_restarted"

    successful_start = system.start_training().json()
    successful = system.wait_training(successful_start["training_run_id"])
    assert successful["status"] == "succeeded"
    assert successful["sample_count"] == 2


def test_training_activation_failure_is_reported(online_rl_system: OnlineRLSystem) -> None:
    system = online_rl_system
    system.start_service()
    _publish_terminal_samples(system, session_id="session-activation-failure", count=2)
    system.set_lora_status(500)

    started = system.start_training().json()
    failed = system.wait_training(started["training_run_id"])

    assert failed["status"] == "failed"
    assert "activation status 502" in failed["failure_reason"]
    assert system.lora().json()["status"] == "base"
    assert [path for path, _ in system.lora_control_calls].count("/v1/load_lora_adapter") == 2


def test_training_fixed_batch_isolates_new_mixed_policy_samples(
    online_rl_system: OnlineRLSystem,
) -> None:
    system = online_rl_system
    system.start_service()
    pinned_base = system.start_task(session_id="session-mixed-base", reward_mode="terminal").json()
    _publish_terminal_samples(system, session_id="session-build-v1", count=2)
    first = system.start_training().json()
    assert system.wait_training(first["training_run_id"])["status"] == "succeeded"

    pinned_v1 = system.start_task(session_id="session-mixed-v1", reward_mode="terminal").json()
    assert pinned_v1["policy_lora_name"] == "model-1:v1"
    for session_id in ("session-mixed-base", "session-mixed-v1"):
        assert (
            system.complete(
                messages=[{"role": "user", "content": session_id}],
                session_id=session_id,
            ).status_code
            == 200
        )
    for task in (pinned_base, pinned_v1):
        assert system.stop_task(task["rl_task_id"]).status_code == 200
        assert system.reward(task["rl_task_id"], 1.0).status_code == 200

    system.set_ppo_mode("block")
    started = system.start_training().json()
    running = _wait_json(
        lambda: system.training(started["training_run_id"]),
        lambda body: body["status"] == "running",
    )
    assert running["sample_count"] == 2
    assert running["policy_versions"] == {"base": 1, "model-1:v1": 1}

    _publish_terminal_samples(system, session_id="session-arrived-during-run", count=1)
    stats = system.trajectory_stats().json()
    assert stats["by_status"]["training"] == 2
    assert stats["by_status"]["pending"] == 1

    system.set_ppo_mode("success")
    completed = system.wait_training(started["training_run_id"])
    assert completed["status"] == "succeeded"
    assert completed["sample_count"] == 2
    assert system.trajectory_stats().json()["by_status"]["pending"] == 1


def test_activating_run_recovers_after_aigw_abnormal_exit(
    online_rl_system: OnlineRLSystem,
) -> None:
    system = online_rl_system
    system.start_service()
    _publish_terminal_samples(system, session_id="session-activating-restart", count=2)
    system.set_fault("activation", "block")
    started = system.start_training().json()
    activating = _wait_json(
        lambda: system.training(started["training_run_id"]),
        lambda body: body["stage"] == "activating",
    )
    assert activating["status"] == "running"

    system.crash_gateway()
    system.wait_service_process_exit()
    system.set_fault("activation", "success")
    system.restart_gateway()
    assert system.start_service().json()["status"] == "running"

    recovered = system.training(started["training_run_id"]).json()
    assert recovered["status"] == "succeeded"
    assert recovered["stage"] == "activating"
    assert system.lora().json()["active_lora"]["lora_name"] == "model-1:v1"
    assert system.complete(messages=[{"role": "user", "content": "gateway recovered"}]).status_code == 200


def test_lora_dynamic_instance_partial_rollback_and_degraded_restart(
    online_rl_system: OnlineRLSystem,
) -> None:
    system = online_rl_system
    system.start_service()
    _publish_terminal_samples(system, session_id="session-lora-v1", count=2)
    first = system.start_training().json()
    assert system.wait_training(first["training_run_id"])["status"] == "succeeded"

    dynamic = system.add_vllm(lora_status=500)
    degraded = system.lora().json()
    assert degraded["status"] == "degraded"
    assert degraded["instances"] == {"ready": 2, "failed": 1}
    assert any(path == "/v1/load_lora_adapter" for path, _ in system.lora_calls(dynamic))
    system.set_lora_instance_status(dynamic, 200)
    synchronized = system.lora().json()
    assert synchronized["status"] == "active"
    assert synchronized["instances"] == {"ready": 3, "failed": 0}

    _publish_terminal_samples(system, session_id="session-lora-v2", count=2)
    system.set_lora_instance_status(1, 500)
    second = system.start_training().json()
    failed = system.wait_training(second["training_run_id"])
    assert failed["status"] == "failed"
    assert "activation status 502" in failed["failure_reason"]
    for index in (0, dynamic):
        assert any(
            path == "/v1/unload_lora_adapter" and payload["lora_name"] == "model-1:v2"
            for path, payload in system.lora_calls(index)
        )
    system.set_lora_instance_status(1, 200)
    assert system.lora().json()["active_lora"]["lora_name"] == "model-1:v1"

    system.set_lora_instance_status(1, 500)
    system.crash_gateway()
    system.wait_service_process_exit()
    system.restart_gateway()
    restored = system.lora().json()
    assert restored["status"] == "degraded"
    assert restored["instances"] == {"ready": 2, "failed": 1}
    failed_instance_completions = system.completion_count(1)
    assert system.start_service().json()["status"] == "running"
    task = system.start_task(session_id="session-degraded-route", reward_mode="terminal").json()
    for _ in range(4):
        assert (
            system.complete(
                messages=[{"role": "user", "content": "avoid failed instance"}],
                session_id="session-degraded-route",
            ).status_code
            == 200
        )
    assert system.completion_count(1) == failed_instance_completions
    assert system.stop_task(task["rl_task_id"]).status_code == 200
