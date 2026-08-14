# coding: utf-8

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_utils import (
    FIXTURE_DIR,
    call_vllm_chat,
    extract_logprobs,
    extract_prompt_token_ids,
    extract_signature,
    load_json,
)


pytestmark = [pytest.mark.a5_precision]


PROMPTS = load_json(FIXTURE_DIR / "a5_precision_prompts.json")


@pytest.mark.parametrize("case", PROMPTS, ids=[item["id"] for item in PROMPTS])
def test_direct_vllm_greedy_output_is_repeatable(case: dict):
    first = extract_signature(call_vllm_chat(case["messages"]))
    second = extract_signature(call_vllm_chat(case["messages"]))

    assert first == second
    assert first.text.strip(), "completion text must not be empty"
    assert first.token_ids, "vLLM must return completion token ids for online RL Rail"


def test_direct_vllm_prompt_order_is_stable(tmp_path: Path):
    forward = {
        case["id"]: extract_signature(call_vllm_chat(case["messages"])).to_json()
        for case in PROMPTS
    }
    reverse = {
        case["id"]: extract_signature(call_vllm_chat(case["messages"])).to_json()
        for case in reversed(PROMPTS)
    }

    (tmp_path / "forward.json").write_text(json.dumps(forward, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp_path / "reverse.json").write_text(json.dumps(reverse, ensure_ascii=False, indent=2), encoding="utf-8")
    assert forward == reverse


def test_direct_vllm_token_metadata_is_present_and_stable():
    case = next(item for item in PROMPTS if item["id"] == "tool_call_like_json")
    first_response = call_vllm_chat(case["messages"])
    second_response = call_vllm_chat(case["messages"])

    first = extract_signature(first_response)
    second = extract_signature(second_response)
    first_prompt_ids = extract_prompt_token_ids(first_response)
    second_prompt_ids = extract_prompt_token_ids(second_response)
    first_logprobs = extract_logprobs(first_response)
    second_logprobs = extract_logprobs(second_response)

    assert first == second
    assert first_prompt_ids == second_prompt_ids
    assert first_prompt_ids, "vLLM must return prompt token ids when return_token_ids=true"
    assert first_logprobs == second_logprobs
    assert len(first_logprobs) in {0, len(first.token_ids)}
