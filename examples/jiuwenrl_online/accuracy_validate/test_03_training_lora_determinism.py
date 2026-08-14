# coding: utf-8

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from st_utils import (
    FIXTURE_DIR,
    adapter_manifest,
    call_vllm_chat,
    compare_adapter_tensors,
    extract_signature,
    load_lora_adapter,
    maybe_copy_artifacts,
    run_direct_training,
)


pytestmark = [pytest.mark.a5_precision, pytest.mark.training]


@pytest.fixture(scope="session")
def trained_lora_pair(tmp_path_factory):
    if os.getenv("ST_TEST_RUN_TRAINING", "0") != "1":
        pytest.skip("set ST_TEST_RUN_TRAINING=1 to run direct PPO training ST")
    work_dir = tmp_path_factory.mktemp("jiuwenrl_online_lora_st")
    fixture = FIXTURE_DIR / "a5_training_trajectories.json"
    first = run_direct_training(fixture=fixture, work_dir=work_dir, run_name="repeat_a")
    second = run_direct_training(fixture=fixture, work_dir=work_dir, run_name="repeat_b")
    yield work_dir, first, second
    maybe_copy_artifacts(work_dir)


def test_direct_training_produces_complete_lora_adapter(trained_lora_pair):
    work_dir, first, _ = trained_lora_pair
    manifest = adapter_manifest(first)
    (work_dir / "manifest_first.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert manifest["adapter_config"]["peft_type"] == "LORA"
    assert manifest["metadata_without_timestamp"]["trajectory_count"] == 4
    assert manifest["metadata_without_timestamp"]["reward_avg"] == pytest.approx(0.8625)
    assert manifest["adapter_model_sha256"]


def test_repeated_direct_training_lora_is_numerically_stable(trained_lora_pair):
    work_dir, first, second = trained_lora_pair
    first_manifest = adapter_manifest(first)
    second_manifest = adapter_manifest(second)
    tensor_diff = compare_adapter_tensors(first, second)
    report = {
        "first": first_manifest,
        "second": second_manifest,
        "tensor_diff": tensor_diff,
    }
    (work_dir / "lora_repeat_compare.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    first_config = dict(first_manifest["adapter_config"])
    second_config = dict(second_manifest["adapter_config"])
    assert first_config == second_config
    assert tensor_diff["left_only"] == []
    assert tensor_diff["right_only"] == []
    assert tensor_diff["common_tensors"] > 0

    strict_numeric = os.getenv("ST_TEST_STRICT_LORA_NUMERIC", "0") == "1"
    if os.getenv("ST_TEST_REQUIRE_EXACT_LORA_HASH", "0") == "1":
        assert first_manifest["adapter_model_sha256"] == second_manifest["adapter_model_sha256"]
    max_abs_threshold = float(os.getenv("ST_TEST_LORA_MAX_ABS_DIFF", "1e-4"))
    if strict_numeric:
        assert tensor_diff["max_abs"] <= max_abs_threshold


def test_repeated_lora_hotload_outputs_match(trained_lora_pair):
    work_dir, first, second = trained_lora_pair
    first_name = "st-a5-repeat-a"
    second_name = "st-a5-repeat-b"

    load_lora_adapter(first_name, first)
    load_lora_adapter(second_name, second)

    messages = [
        {"role": "system", "content": "你是一个严谨的代码助手。只输出最终答案。"},
        {"role": "user", "content": "/no_think\n用两句话说明在线RL精度验证为什么要固定输入输出。"},
    ]
    first_output = extract_signature(call_vllm_chat(messages, model=first_name)).to_json()
    second_output = extract_signature(call_vllm_chat(messages, model=second_name)).to_json()
    (work_dir / "hotload_probe_outputs.json").write_text(
        json.dumps({"first": first_output, "second": second_output}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert first_output == second_output
