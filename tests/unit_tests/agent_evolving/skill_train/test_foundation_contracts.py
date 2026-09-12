# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.skill_train.datasets.base import (
    BaseDataLoader,
    SplitDataLoader,
    _allocate_partitions_by_ratio,
)
from openjiuwen.agent_evolving.skill_train.gate import (
    GateState,
    compute_semantic_density,
    evaluate_gate,
    select_gate_score,
)
from openjiuwen.agent_evolving.skill_train.skill_patch import (
    APPENDIX_START,
    SLOW_UPDATE_START,
    apply_patch,
    apply_patch_with_report,
)
from openjiuwen.agent_evolving.skill_train.types import (
    Edit,
    FailureSummaryEntry,
    Patch,
    RawPatch,
    RolloutResult,
    SlowUpdateResult,
)
from openjiuwen.agent_evolving.skill_train import update_modes as um


# ---------------------------------------------------------------------------
# types.py
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_edit_from_dict_to_dict_roundtrip_omits_empty_optionals() -> None:
    sparse = Edit.from_dict({"op": "append", "content": "rule"})
    assert sparse.to_dict() == {"op": "append", "content": "rule"}

    full = Edit.from_dict(
        {
            "op": "replace",
            "content": "new",
            "target": "old",
            "support_count": 3,
            "source_type": "failure",
            "merge_level": 1,
            "update_origin": "analyst",
            "update_target": "body",
        }
    )
    assert full.to_dict() == {
        "op": "replace",
        "content": "new",
        "target": "old",
        "support_count": 3,
        "source_type": "failure",
        "merge_level": 1,
        "update_origin": "analyst",
        "update_target": "body",
    }
    assert Edit.from_dict(full.to_dict()).to_dict() == full.to_dict()


@pytest.mark.level0
def test_patch_and_failure_summary_roundtrip() -> None:
    entry = FailureSummaryEntry.from_dict(
        {"failure_type": "parse", "count": 2, "description": "bad json"}
    )
    assert entry.to_dict() == {
        "failure_type": "parse",
        "count": 2,
        "description": "bad json",
    }

    patch = Patch.from_dict(
        {
            "reasoning": "fix parse",
            "edits": [{"op": "append", "content": "A"}],
            "ranking_details": {"score": 0.9},
        }
    )
    assert patch.to_dict() == {
        "reasoning": "fix parse",
        "edits": [{"op": "append", "content": "A"}],
        "ranking_details": {"score": 0.9},
    }
    bare = Patch.from_dict({"edits": []})
    assert "ranking_details" not in bare.to_dict()


@pytest.mark.level0
def test_raw_patch_roundtrip_and_none_input() -> None:
    assert RawPatch.from_dict(None) is None
    assert RawPatch.from_dict({"patch": "not-a-dict"}) is None

    raw = RawPatch.from_dict(
        {
            "patch": {"reasoning": "r", "edits": [{"op": "delete", "target": "x", "content": ""}]},
            "source_type": "success",
            "batch_size": 4,
            "failure_summary": [{"failure_type": "timeout", "count": 1}],
        }
    )
    assert raw is not None
    payload = raw.to_dict()
    assert payload["source_type"] == "success"
    assert payload["batch_size"] == 4
    assert payload["failure_summary"] == [
        {"failure_type": "timeout", "count": 1, "description": ""}
    ]
    assert RawPatch.from_dict(payload).to_dict() == payload

    no_summary = RawPatch.from_dict({"edits": [{"op": "append", "content": "z"}]})
    assert no_summary is not None
    assert "failure_summary" not in no_summary.to_dict()


@pytest.mark.level0
def test_rollout_result_extras_spillover_and_omits_empty_optionals() -> None:
    result = RolloutResult.from_dict(
        {
            "id": "ep-1",
            "hard": 1,
            "soft": 0.5,
            "n_turns": 0,
            "fail_reason": "",
            "custom_metric": 0.42,
            "env_tag": "docvqa",
        }
    )
    assert result.extras == {"custom_metric": 0.42, "env_tag": "docvqa"}
    serialized = result.to_dict()
    assert serialized == {
        "id": "ep-1",
        "hard": 1.0,
        "soft": 0.5,
        "custom_metric": 0.42,
        "env_tag": "docvqa",
    }
    assert "n_turns" not in serialized
    assert "fail_reason" not in serialized

    rich = RolloutResult.from_dict(
        {
            "id": "ep-2",
            "hard": 0,
            "soft": 0.1,
            "n_turns": 3,
            "fail_reason": "timeout",
            "predicted_answer": "42",
            "trace_id": "t1",
        }
    )
    roundtripped = RolloutResult.from_dict(rich.to_dict())
    assert roundtripped.to_dict() == rich.to_dict()
    assert roundtripped.extras == {"trace_id": "t1"}
    assert roundtripped.n_turns == 3
    assert roundtripped.fail_reason == "timeout"


@pytest.mark.level0
def test_slow_update_result_roundtrip_omits_blank_fields() -> None:
    assert SlowUpdateResult.from_dict(None) is None
    empty = SlowUpdateResult.from_dict({})
    assert empty is not None
    assert empty.to_dict() == {"reasoning": "", "slow_update_content": ""}

    full = SlowUpdateResult.from_dict(
        {
            "reasoning": "improve",
            "slow_update_content": "MUST verify",
            "action": "accept",
            "time_s": 1.5,
            "prev_hard": 0.2,
            "curr_hard": 0.4,
            "selection_hard": 0.4,
            "selection_soft": 0.3,
            "candidate_hash": "abc",
            "update_origin": "slow",
            "update_target": "skill",
        }
    )
    assert SlowUpdateResult.from_dict(full.to_dict()).to_dict() == full.to_dict()


# ---------------------------------------------------------------------------
# gate.py
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_evaluate_gate_reject_accept_accept_new_best() -> None:
    state = GateState(
        current_skill="cur",
        current_score=0.5,
        best_skill="best",
        best_score=0.8,
        best_step=3,
    )
    rejected = evaluate_gate("cand", 0.5, state, global_step=10)
    assert rejected.action == "reject"
    assert rejected.current_skill == "cur"
    assert rejected.current_score == 0.5
    assert rejected.best_skill == "best"
    assert rejected.best_score == 0.8
    assert rejected.best_step == 3

    accepted = evaluate_gate("cand", 0.6, state, global_step=10)
    assert accepted.action == "accept"
    assert accepted.current_skill == "cand"
    assert accepted.current_score == 0.6
    assert accepted.best_skill == "best"
    assert accepted.best_score == 0.8
    assert accepted.best_step == 3

    new_best = evaluate_gate("cand", 0.9, state, global_step=10)
    assert new_best.action == "accept_new_best"
    assert new_best.current_skill == "cand"
    assert new_best.current_score == 0.9
    assert new_best.best_skill == "cand"
    assert new_best.best_score == 0.9
    assert new_best.best_step == 10


@pytest.mark.level0
def test_select_gate_score_hard_soft_mixed() -> None:
    assert select_gate_score(0.8, 0.2, metric="hard") == 0.8
    assert select_gate_score(0.8, 0.2, metric="soft") == 0.2
    assert select_gate_score(0.8, 0.2, metric="mixed", mixed_weight=0.5) == pytest.approx(0.5)
    assert select_gate_score(1.0, 0.0, metric="mixed", mixed_weight=0.25) == pytest.approx(0.75)
    assert select_gate_score(0.0, 1.0, metric="mixed", mixed_weight=1.5) == pytest.approx(1.0)
    assert select_gate_score(1.0, 0.0, metric="mixed", mixed_weight=-1.0) == pytest.approx(1.0)


@pytest.mark.level0
def test_compute_semantic_density_strips_slow_update_markers() -> None:
    body = (
        "ALWAYS check answers. "
        "<!-- SLOW_UPDATE_START --> MUST MUST MUST CRITICAL IMPORTANT <!-- SLOW_UPDATE_END --> "
        "NEVER guess."
    )
    density = compute_semantic_density(body)
    # After stripping slow-update block: ALWAYS, check, answers, NEVER, guess → 2/5
    assert density == pytest.approx(0.4)

    empty = compute_semantic_density("   ")
    assert empty == 0.0

    custom = compute_semantic_density("foo bar foo", leading_words=["foo"])
    assert custom == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# update_modes.py
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_normalize_update_mode_aliases() -> None:
    assert um.normalize_update_mode(None) == um.PATCH_MODE
    assert um.normalize_update_mode("") == um.PATCH_MODE
    assert um.normalize_update_mode("EDITS") == um.PATCH_MODE
    assert um.normalize_update_mode("rewrite") == um.REWRITE_MODE
    assert um.normalize_update_mode("suggestions") == um.REWRITE_MODE
    assert um.normalize_update_mode("full_rewrite") == um.FULL_REWRITE_MINIBATCH_MODE
    assert um.normalize_update_mode("skill_rewrite_minibatch") == um.FULL_REWRITE_MINIBATCH_MODE
    assert um.normalize_update_mode("unknown-mode") == um.PATCH_MODE
    assert um.is_rewrite_mode("rewrite_from_suggestions")
    assert um.is_full_rewrite_minibatch_mode("minibatch_full_rewrite")
    assert not um.is_rewrite_mode("patch")


@pytest.mark.level0
def test_payload_key_get_set_and_truncate() -> None:
    assert um.payload_key("patch") == "edits"
    assert um.payload_key("rewrite") == "revise_suggestions"
    assert um.payload_key("full_rewrite_minibatch") == "skill_candidates"

    container = {"edits": [{"op": "append"}, {"op": "delete"}, {"op": "replace"}]}
    assert len(um.get_payload_items(container, "patch")) == 3
    assert um.get_payload_items(None, "patch") == []
    assert um.get_payload_items({"edits": "bad"}, "patch") == []

    um.set_payload_items(container, [{"op": "a"}, {"op": "b"}, {"op": "c"}], "edits")
    trimmed = um.truncate_payload(container, 2, "patch")
    assert trimmed["edits"] == [{"op": "a"}, {"op": "b"}]
    assert um.truncate_payload(trimmed, -1, "patch") is trimmed
    assert um.truncate_payload(trimmed, 10, "patch") is trimmed


@pytest.mark.level0
def test_describe_item_and_short_item_summary_per_mode() -> None:
    patch_item = {"op": "replace", "target": "old", "content": "new", "support_count": 2}
    assert um.describe_item(patch_item, "patch") == "op=replace  target='old'  content='new'  support=2"
    assert um.short_item_summary(patch_item, "patch") == {
        "op": "replace",
        "content": "new",
        "target": "old",
    }

    rewrite_item = {
        "type": "add",
        "title": "t1",
        "instruction": "do x",
        "priority_hint": "high",
        "support_count": 1,
    }
    assert (
        um.describe_item(rewrite_item, "rewrite")
        == "type=add  title='t1'  instruction='do x'  priority=high  support=1"
    )
    assert um.short_item_summary(rewrite_item, "rewrite") == {
        "type": "add",
        "title": "t1",
        "instruction": "do x",
    }

    mini_item = {
        "title": "cand",
        "change_summary": ["a", "b"],
        "source_type": "failure",
        "support_count": 4,
        "new_skill": "full text",
    }
    assert (
        um.describe_item(mini_item, "full_rewrite")
        == "title='cand'  change_summary=['a', 'b']  source=failure  support=4  "
        "new_skill_preview='full text'"
    )
    assert um.short_item_summary(mini_item, "full_rewrite") == {
        "title": "cand",
        "change_summary": ["a", "b"],
        "source_type": "failure",
    }
    assert um.describe_item("not-a-dict", "patch") == ""


# ---------------------------------------------------------------------------
# skill_patch.py
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_apply_patch_append_insert_after_replace_delete() -> None:
    skill = "# Skill\n\n## Rules\nKeep calm.\n"
    updated = apply_patch(
        skill,
        {
            "edits": [
                {"op": "append", "content": "ALWAYS verify."},
                {"op": "insert_after", "target": "## Rules", "content": "Be precise."},
                {"op": "replace", "target": "Keep calm.", "content": "Stay focused."},
                {"op": "delete", "target": "ALWAYS verify.\n"},
            ]
        },
    )
    assert "## Rules\n\nBe precise.\n" in updated
    assert "Stay focused." in updated
    assert "Keep calm." not in updated
    assert "ALWAYS verify." not in updated


@pytest.mark.level0
def test_apply_patch_skips_protected_region_targets() -> None:
    skill = (
        "# Skill\n"
        "<!-- SLOW_UPDATE_START -->\n"
        "protected line\n"
        "<!-- SLOW_UPDATE_END -->\n"
        f"{APPENDIX_START}\n"
        "appendix body\n"
        "<!-- APPENDIX_END -->\n"
    )
    updated, reports = apply_patch_with_report(
        skill,
        {"edits": [{"op": "replace", "target": "protected line", "content": "hacked"}]},
    )
    assert updated == skill
    assert reports[0]["status"] == "skipped_protected_region"


@pytest.mark.level0
def test_apply_patch_append_inserts_before_slow_update_markers() -> None:
    skill = (
        "# Skill\n\n"
        "<!-- SLOW_UPDATE_START -->\n"
        "slow body\n"
        "<!-- SLOW_UPDATE_END -->\n"
    )
    updated, reports = apply_patch_with_report(
        skill,
        {"edits": [{"op": "append", "content": "new rule"}]},
    )
    assert reports[0]["status"] == "applied_append_before_protected_region"
    assert updated.index("new rule") < updated.index(SLOW_UPDATE_START)
    assert "<!-- SLOW_UPDATE_START -->\nslow body\n<!-- SLOW_UPDATE_END -->" in updated


# ---------------------------------------------------------------------------
# datasets/base.py — SplitDataLoader
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_make_base_seeds_and_shuffle_epoch_seeds_deterministic() -> None:
    base = BaseDataLoader.make_base_seeds(steps_per_epoch=2, accumulation=3, seed=10)
    assert base == [11, 12, 13, 14, 15, 16]
    first = BaseDataLoader.shuffle_epoch_seeds(base, epoch=1, seed=10)
    second = BaseDataLoader.shuffle_epoch_seeds(base, epoch=1, seed=10)
    assert first == second
    other_epoch = BaseDataLoader.shuffle_epoch_seeds(base, epoch=2, seed=10)
    assert other_epoch != first
    # Mimic algorithm: Random(seed + epoch * 1000).shuffle
    expected = list(base)
    random.Random(10 + 1 * 1000).shuffle(expected)
    assert first == expected


@pytest.mark.level0
def test_ratio_split_2_1_7_seed_42_on_ten_items(tmp_path: Path) -> None:
    items = [{"id": str(i), "q": f"q{i}"} for i in range(10)]
    data_path = tmp_path / "items.json"
    data_path.write_text(json.dumps(items), encoding="utf-8")
    out_root = tmp_path / "out"

    # Mimic production allocation for expected counts/slices.
    shuffled = list(items)
    random.Random(42).shuffle(shuffled)
    n_train, n_val, n_test = _allocate_partitions_by_ratio(10, (2, 1, 7))
    assert (n_train, n_val, n_test) == (2, 1, 7)
    expected_train = shuffled[:n_train]
    expected_val = shuffled[n_train : n_train + n_val]
    expected_test = shuffled[n_train + n_val :]

    loader = SplitDataLoader(
        data_path=str(data_path),
        split_mode="ratio",
        split_ratio="2:1:7",
        split_seed=42,
        split_output_dir=str(tmp_path / "split"),
    )
    loader.setup({"out_root": str(out_root), "env": "unit"})

    assert [row["id"] for row in loader.train_items] == [row["id"] for row in expected_train]
    assert [row["id"] for row in loader.val_items] == [row["id"] for row in expected_val]
    assert [row["id"] for row in loader.test_items] == [row["id"] for row in expected_test]

    train_ids = {row["id"] for row in loader.train_items}
    val_ids = {row["id"] for row in loader.val_items}
    test_ids = {row["id"] for row in loader.test_items}
    assert len(loader.train_items) + len(loader.val_items) + len(loader.test_items) == 10
    assert train_ids.isdisjoint(val_ids)
    assert train_ids.isdisjoint(test_ids)
    assert val_ids.isdisjoint(test_ids)
    assert train_ids | val_ids | test_ids == {str(i) for i in range(10)}


@pytest.mark.level0
def test_build_train_and_eval_batch_sizes(tmp_path: Path) -> None:
    split_dir = tmp_path / "splits"
    for name, count in (("train", 8), ("val", 5), ("test", 4)):
        folder = split_dir / name
        folder.mkdir(parents=True)
        payload = [{"id": f"{name}-{i}"} for i in range(count)]
        (folder / "items.json").write_text(json.dumps(payload), encoding="utf-8")

    loader = SplitDataLoader(split_dir=str(split_dir), split_mode="split_dir")
    loader.setup({})

    train_batch = loader.build_train_batch(batch_size=3, seed=7)
    assert train_batch.phase == "train"
    assert train_batch.split == "train"
    assert train_batch.batch_size == 3
    assert len(train_batch.payload) == 3

    oversized = loader.build_train_batch(batch_size=100, seed=7)
    assert oversized.batch_size == 8
    assert len(oversized.payload) == 8

    eval_batch = loader.build_eval_batch(env_num=2, split="val", seed=1)
    assert eval_batch.phase == "eval"
    assert eval_batch.split == "val"
    assert eval_batch.batch_size == 2
    assert len(eval_batch.payload) == 2

    full_eval = loader.build_eval_batch(env_num=0, split="test", seed=1)
    assert full_eval.batch_size == 4


@pytest.mark.level0
def test_plan_train_epoch_slot_count_equals_steps_times_accumulation(tmp_path: Path) -> None:
    split_dir = tmp_path / "splits"
    for name in ("train", "val", "test"):
        folder = split_dir / name
        folder.mkdir(parents=True)
        payload = [{"id": f"{name}-{i}"} for i in range(6)]
        (folder / "items.json").write_text(json.dumps(payload), encoding="utf-8")

    loader = SplitDataLoader(split_dir=str(split_dir), split_mode="split_dir")
    loader.setup({})

    steps, accumulation = 3, 2
    planned = loader.plan_train_epoch(
        epoch=0,
        steps_per_epoch=steps,
        accumulation=accumulation,
        batch_size=2,
        seed=42,
    )
    assert len(planned) == steps * accumulation
    assert all(spec.phase == "train" for spec in planned)
    assert all(spec.batch_size == 2 for spec in planned)
