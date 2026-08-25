from __future__ import annotations

import asyncio
from pathlib import Path


class _AsyncEvaler:
    async def evaluate(self, request):
        from openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins import EvalResult

        assert request.lora_id == "u1:v1"
        return EvalResult(passed=False, score=0.1, target_score=0.8, reason="too low")


def _make_lora_dir(tmp_path: Path) -> str:
    lora_dir = tmp_path / "adapter"
    lora_dir.mkdir()
    (lora_dir / "adapter_model.safetensors").write_text("dummy")
    return str(lora_dir)


def test_evaler_result_marks_lora_unavailable(tmp_path):
    from openjiuwen.agent_evolving.agent_rl.online.backends.rl.trainer import PPOTrainingExecutor
    from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRAPublishRequest, LoRARepository

    repo = LoRARepository(str(tmp_path / "repo"))
    version = repo.publish(LoRAPublishRequest(user_id="u1", lora_path=_make_lora_dir(tmp_path)))
    executor = PPOTrainingExecutor(
        base_model_path="/base/model",
        lora_repo=repo,
        notifier=None,
        nproc_per_node=1,
        training_gpu_ids="",
        ppo_config_path=None,
        evaler=_AsyncEvaler(),
    )

    evaluated = asyncio.run(
        executor._maybe_eval_lora(
            user_id="u1",
            samples=[{"sample_id": "s1"}],
            training_count=1,
            version=version,
        )
    )

    assert evaluated.availability_status == "unavailable"
    assert evaluated.availability_reason == "too low"
    assert repo.get_latest_available("u1") is None


def test_plugin_coercion_accepts_dict_results():
    from openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins import (
        coerce_eval_result,
        coerce_rollout_result,
    )

    rollout = coerce_rollout_result({"trajectories": [{"sample_id": "s2"}], "metrics": {"n": 1}})
    eval_result = coerce_eval_result({"passed": True, "score": 0.9, "target_score": 0.8})

    assert rollout.success is True
    assert rollout.trajectories == [{"sample_id": "s2"}]
    assert eval_result.passed is True
    assert eval_result.score == 0.9
    assert eval_result.target_score == 0.8
