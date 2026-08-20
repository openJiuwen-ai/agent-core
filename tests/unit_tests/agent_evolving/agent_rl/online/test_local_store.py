from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_local_store_shares_samples_between_instances(tmp_path):
    from openjiuwen.agent_evolving.agent_rl.storage.local_store import LocalTrajectoryStore

    writer = LocalTrajectoryStore(tmp_path)
    reader = LocalTrajectoryStore(tmp_path)

    await writer.save_sample(
        {
            "sample_id": "s1",
            "user_id": "u1",
            "model": "m1",
            "source": "rail-v1",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        user_id="u1",
    )

    assert await reader.get_pending_count("u1") == 1
    assert await reader.get_users_above_threshold(1) == ["u1"]

    samples = await reader.fetch_and_mark_training("u1", 4)
    assert [sample["sample_id"] for sample in samples] == ["s1"]
    assert samples[0]["_store_status"] == "training"

    await writer.mark_trained(["s1"])
    stats = await reader.stats()
    assert stats["trained_samples"] == 1
    assert stats["pending_samples"] == 0


@pytest.mark.asyncio
async def test_local_training_task_store_preserves_single_active_task(tmp_path):
    from openjiuwen.agent_evolving.agent_rl.storage.local_store import LocalTrainingTaskStore

    writer = LocalTrainingTaskStore(tmp_path)
    reader = LocalTrainingTaskStore(tmp_path)

    task = await writer.create_task({"task_id": "task-1", "user_id": "u1"})
    assert task["status"] == "pending"
    assert (await reader.get_active_task())["task_id"] == "task-1"

    with pytest.raises(RuntimeError):
        await reader.create_task({"task_id": "task-2"})

    claimed = await reader.claim_pending_task(user_id="u1", sample_count=3)
    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["sample_count"] == 3

    stopped = await writer.request_stop("task-1")
    assert stopped is not None
    assert stopped["status"] == "stopping"


@pytest.mark.asyncio
async def test_local_pending_judge_store_preserves_session_order(tmp_path):
    from openjiuwen.agent_evolving.agent_rl.storage.local_store import LocalPendingJudgeStore

    writer = LocalPendingJudgeStore(tmp_path)
    reader = LocalPendingJudgeStore(tmp_path)

    await writer.put({"session_id": "sess", "trajectory_id": "traj", "step_index": 1})
    await writer.put({"session_id": "sess", "trajectory_id": "traj", "step_index": 2})

    first = await reader.pop_earliest("sess")
    assert first is not None
    assert first["step_index"] == 1

    remaining = await reader.pop_all("sess")
    assert [sample["step_index"] for sample in remaining] == [2]


@pytest.mark.asyncio
async def test_local_store_backs_up_corrupted_state_before_starting_fresh(tmp_path, caplog):
    from openjiuwen.agent_evolving.agent_rl.storage.local_store import LocalTrajectoryStore

    state_path = tmp_path / "online_rl_store.json"
    state_path.write_text("{not json", encoding="utf-8")

    store = LocalTrajectoryStore(tmp_path)
    with caplog.at_level("WARNING", logger="openjiuwen.agent_evolving.agent_rl.storage.local_store"):
        stats = await store.stats()

    backup_path = tmp_path / "online_rl_store.json.bak"
    assert stats["total_samples"] == 0
    assert backup_path.read_text(encoding="utf-8") == "{not json"
    assert json.loads(state_path.read_text(encoding="utf-8"))["samples"] == {}
    assert "starting with empty state" in caplog.text
