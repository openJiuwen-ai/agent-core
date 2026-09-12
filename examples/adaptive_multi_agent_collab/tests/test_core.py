from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from collections.abc import Iterator

import pytest
import torch
from torch.nn import functional as F

from examples.adaptive_multi_agent_collab.config import ExperimentConfig
from examples.adaptive_multi_agent_collab.evaluation import (
    attribute_usage,
    classify_transition,
    plurality_vote,
    weighted_vote,
)
from examples.adaptive_multi_agent_collab.experiment import (
    _settled_gather,
    select_dataset_splits,
    synthetic_splits,
)
from examples.adaptive_multi_agent_collab.openjiuwen_client import (
    ApiCallBudget,
    BudgetExhausted,
    OpenJiuwenClient,
    usage_to_dict,
)
from examples.adaptive_multi_agent_collab.run_experiment import (
    STATUS_MOCK,
    _arguments,
    _config,
    _report,
)
from examples.adaptive_multi_agent_collab.schemas import (
    AnswerParser,
    CacheKey,
    CallRecord,
    JsonlCallCache,
    MCQExample,
    ParsedReviewer,
    Trajectory,
    validate_reviewer_protocol,
)
from examples.adaptive_multi_agent_collab.weighting import (
    SignedHashEncoder,
    WeightingConfig,
    WeightingModel,
    modal_set,
    perspective_history,
    support_components,
    terminal_support_tensor,
)

@pytest.fixture
def local_tmp() -> Iterator[Path]:
    path = Path(__file__).parents[1] / "tmp" / "tests" / uuid.uuid4().hex
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path)


def example(identifier: str = "x", gold: str = "A") -> MCQExample:
    return MCQExample(identifier, "train", "Which option?", dict(zip("ABCDE", "abcde")), gold)


@pytest.mark.parametrize(
    ("content", "structured", "answer", "method"),
    [
        ("ignored", {"answer": "A", "justification": "short"}, "A", "structured"),
        ('{"answer":"b","justification":"short"}', None, "B", "content_json"),
        ("malformed {json}\nFinal answer: c", None, "C", "explicit_marker"),
        ("answer = d", None, "D", "explicit_marker"),
        (" e. ", None, "E", "isolated_label"),
    ],
)
def test_answer_parsing(content, structured, answer, method):
    parsed = AnswerParser().parse_answer(content, structured)
    assert (parsed.answer, parsed.parse_method) == (answer, method)


@pytest.mark.parametrize("content", ["A cat sat there.", "please compare every option", "maybe"])
def test_answer_parser_rejects_arbitrary_letters(content):
    assert AnswerParser().parse_answer(content).answer is None


def test_final_answer_marker_without_separator():
    parsed = AnswerParser().parse_answer("After checking, Final answer B")
    assert (parsed.answer, parsed.parse_method) == ("B", "explicit_marker")


def test_reviewer_parsing_and_protocol_repair():
    parser = AnswerParser()
    parsed = parser.parse_reviewer(
        '{"status":"complete","feedback":"accepted","recommended_answer":"b"}'
    )
    assert (parsed.status, parsed.recommended_answer) == ("complete", "B")
    inconsistent = validate_reviewer_protocol(parsed, "A")
    assert inconsistent.protocol_inconsistent and inconsistent.status == "complete"
    repaired = validate_reviewer_protocol(parsed, "A", repair=True)
    assert repaired.protocol_repair and repaired.status == "continue"
    fallback = parser.parse_reviewer("Status: continue; recommended answer: c")
    assert (fallback.status, fallback.recommended_answer) == ("continue", "C")


def row(index: int) -> dict:
    return {
        "id": f"id-{index}",
        "question": f"q-{index}",
        "choices": {"label": list("ABCDE"), "text": [f"{label}-{index}" for label in "ABCDE"]},
        "answerKey": "ABCDE"[index % 5],
    }


def test_dataset_split_is_deterministic_and_disjoint():
    config = ExperimentConfig(train_size=3, val_size=2, test_size=2, seed=42)
    first = select_dataset_splits([row(i) for i in range(20)], [row(i + 100) for i in range(10)], config)
    second = select_dataset_splits([row(i) for i in range(20)], [row(i + 100) for i in range(10)], config)
    assert [[item.id for item in first[name]] for name in first] == [
        [item.id for item in second[name]] for name in second
    ]
    ids = [{item.id for item in first[name]} for name in ("train", "validation", "test")]
    assert not (ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])


def test_synthetic_splits_have_requested_sizes():
    splits = synthetic_splits(ExperimentConfig(train_size=2, val_size=1, test_size=2, offline_mock=True))
    assert {name: len(values) for name, values in splits.items()} == {
        "train": 2, "validation": 1, "test": 2
    }
    assert all(item.source_split.startswith("synthetic_") for values in splits.values() for item in values)


def test_plurality_and_deterministic_ties():
    assert plurality_vote(["A", "B", "A"]) == "A"
    assert plurality_vote(["B", "A", "C"], [0, 1, 2], True) == ("B", True)
    assert weighted_vote([0.5, 0.5, 0, 0, 0], ["B", "A", "C"], return_tie=True) == ("B", True)


def test_support_rho_mu_u_and_empty():
    rho, mu, support = support_components(["A", "A", "B", "B"])
    assert modal_set(["A", "A", "B", "B"]) == {"A", "B"}
    assert torch.allclose(rho[:2], torch.tensor([0.5, 0.5]))
    assert torch.allclose(mu[:2], torch.tensor([0.5, 0.5]))
    assert torch.allclose(support[:2], torch.tensor([0.5, 0.5]))
    empty = support_components([])[2]
    assert torch.count_nonzero(empty) == 0
    stacked = terminal_support_tensor([["A"], [], ["C", "C"]])
    assert stacked.shape == (3, 5) and not stacked.requires_grad


def trajectory() -> Trajectory:
    return Trajectory(
        example(),
        initial_turns={
            0: {"answer": "A", "justification": "j0"},
            1: {"answer": "B", "justification": "j1"},
            2: {"answer": "C", "justification": "j2"},
        },
        conversations=[
            {
                "initiator_id": 0, "reviewer_id": 1, "reviewer_status": "continue",
                "reviewer_recommended_answer": "B", "reviewer_feedback": "check B",
                "revision_answer": "B", "terminal_answer": "B",
            }
        ],
        terminal_answers={0: ["B"], 1: ["B"], 2: ["C"]},
    )


def test_fixed_encoders_are_deterministic_and_history_dependent():
    encoder = SignedHashEncoder(64)
    assert torch.equal(encoder.encode("same"), encoder.encode("same"))
    assert not torch.equal(encoder.encode("same"), encoder.encode("different"))
    first = perspective_history(trajectory(), 0)
    second = perspective_history(trajectory(), 1)
    assert first != second and "gold" not in first.lower()


def test_hash_encoder_is_stable_across_processes():
    code = (
        "from examples.adaptive_multi_agent_collab.weighting import SignedHashEncoder;"
        "print(SignedHashEncoder(16).encode('stable input').tolist())"
    )
    first = subprocess.check_output([sys.executable, "-c", code], text=True)
    second = subprocess.check_output([sys.executable, "-c", code], text=True)
    assert first == second


def test_weighting_shape_normalization_and_input_dependence():
    torch.manual_seed(4)
    config = WeightingConfig(query_dim=8, history_dim=8, hidden_dim=12, dropout=0)
    model = WeightingModel(config)
    history = torch.randn(3, 8)
    weights = model(torch.randn(8), history)
    assert weights.shape == (3,)
    assert torch.all(weights > 0) and torch.allclose(weights.sum(), torch.tensor(1.0))
    assert not torch.allclose(weights, model(torch.randn(8), history + 1))


def test_cross_entropy_gradients_only_trainable_path():
    torch.manual_seed(2)
    config = WeightingConfig(query_dim=8, history_dim=8, hidden_dim=12, dropout=0)
    model = WeightingModel(config)
    query, history = torch.randn(8), torch.randn(3, 8)
    support = terminal_support_tensor([["A"], ["B"], ["C"]]).requires_grad_(False)
    scores = model.candidate_scores(query, history, support)
    loss = F.cross_entropy(scores.unsqueeze(0), torch.tensor([0]))
    loss.backward()
    assert loss.item() > 0
    assert model.agent_embeddings.weight.grad is not None
    assert all(parameter.grad is not None for parameter in model.mlp.parameters())
    assert support.grad is None


def key(prompt_hash: str = "p") -> CacheKey:
    return CacheKey("id", "train", "mock", "m", 0, "role", "initial", prompt_hash, {"temperature": 0.2})


def test_trajectory_round_trip():
    value = trajectory()
    value.run_fingerprint = "safe-provenance"
    restored = Trajectory.from_dict(value.to_dict())
    assert restored.example.id == "x" and restored.terminal_answers[0] == ["B"]
    assert restored.run_fingerprint == "safe-provenance"


def test_jsonl_cache_lookup_resume_and_interrupted_line(local_tmp):
    path = local_tmp / "calls.jsonl"
    cache = JsonlCallCache(path, "mock")
    record = CallRecord(key(), "mock", True, 1, "prompt", '{"answer":"A"}', {"answer": "A"}, 0.1)
    cache.append(record)
    path.write_text(path.read_text() + '{"broken":', encoding="utf-8")
    resumed = JsonlCallCache(path, "mock")
    assert resumed.get(key())["parsed"]["answer"] == "A"
    assert resumed.malformed_lines == 1
    assert resumed.get(key("new-prompt")) is None


def test_cache_rejects_secret_fields(local_tmp):
    cache = JsonlCallCache(local_tmp / "calls.jsonl", "mock")
    with pytest.raises(ValueError):
        cache.append({"mode": "mock", "valid": True, "api_key": "forbidden"})


def test_cache_rejects_configured_secret_value(local_tmp, monkeypatch):
    monkeypatch.setenv("API_KEY", "unit-test-placeholder-credential")
    cache = JsonlCallCache(local_tmp / "calls.jsonl", "mock")
    with pytest.raises(ValueError):
        cache.append({
            "mode": "mock", "valid": False,
            "error": "unit-test-placeholder-credential",
        })


def test_transition_categories():
    assert classify_transition("B", "A", "A") == "incorrect -> correct"
    assert classify_transition("A", "B", "A") == "correct -> incorrect"
    assert classify_transition("B", "C", "A") == "incorrect -> incorrect with changed label"
    assert classify_transition("A", "A", "A") == "unchanged"


def test_attributable_usage_accounting():
    data = trajectory().to_dict()
    for agent in range(3):
        data["initial_turns"][agent]["call"] = {
            "stage": "initial", "agent_id": agent, "attempt": 1, "wall_latency": 0.2,
            "usage_metadata": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        }
    data["conversations"][0]["calls"] = [{
        "stage": "review", "agent_id": 1, "attempt": 1, "wall_latency": 0.3,
        "usage_metadata": {"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
    }]
    assert attribute_usage(data, "single_agent")["total_tokens"] == 12
    assert attribute_usage(data, "independent_majority")["calls"] == 3
    assert attribute_usage(data, "collaboration_uniform")["total_tokens"] == 59


def test_api_budget_enforcement():
    async def check():
        budget = ApiCallBudget(1)
        assert await budget.reserve() == 1
        with pytest.raises(BudgetExhausted):
            await budget.reserve()
    asyncio.run(check())


def test_settled_gather_waits_for_siblings_after_failure():
    completed: list[bool] = []

    async def fail():
        raise BudgetExhausted("budget exhausted")

    async def sibling():
        await asyncio.sleep(0.01)
        completed.append(True)

    async def check():
        with pytest.raises(BudgetExhausted):
            await _settled_gather(fail(), sibling())

    asyncio.run(check())
    assert completed == [True]


def test_effective_settings_and_usage_are_secret_free():
    client = OpenJiuwenClient(
        {}, provider="mock", model_name="deterministic-mock", offline_mock=True
    )
    assert client.effective_generation_settings()["temperature"] == 0.2
    assert set(client.base_client_identity()) == {
        "provider", "api_base", "model_name", "verify_ssl"
    }
    usage = usage_to_dict({
        "input_tokens": 2, "total_tokens": 2, "input_cost": 0,
        "output_cost": 0, "total_cost": 0, "total_latency": 0,
    })
    assert usage["total_tokens"] == 2
    assert "total_cost" not in usage and "total_latency" not in usage


def test_artifact_root_cannot_escape_example():
    args = _arguments(["report", "--artifact-root", "/private/tmp/outside-example"])
    with pytest.raises(ValueError, match="must remain under"):
        _config(args)


def test_report_generation_from_synthetic_results(local_tmp):
    config = ExperimentConfig(
        train_size=1, val_size=1, test_size=1, offline_mock=True, artifact_root=local_tmp
    )
    results = config.mode_root / "results"
    results.mkdir(parents=True)
    (results / "training_history.csv").write_text(
        "epoch,train_loss,validation_loss\n1,1.0,1.1\n", encoding="utf-8"
    )
    method = {
        "correct": 1, "evaluated": 1, "failed": 0, "accuracy": 1.0,
        "average_calls": 1.0, "average_total_tokens": 12.0, "average_latency": 0.1,
    }
    summary = {
        "data_status": STATUS_MOCK, "dataset": "synthetic", "sizes": {"train": 1, "validation": 1, "test": 1},
        "manifest": "cache/manifest.json", "seed": 42, "provider": "mock", "model_name": "deterministic-mock",
        "encoder": "signed hashing", "training": {
            "device": "cpu", "best_epoch": 1, "best_train_loss": 1.0,
            "best_validation_loss": 1.1, "test_loss": 1.2, "training_seconds": 0.1,
        },
        "evaluation": {
            "methods": {name: method for name in (
                "single_agent", "independent_majority", "collaboration_uniform", "collaboration_learned"
            )},
            "agents": {}, "agreement": {}, "oracles": {}, "transitions": {},
            "weights": {"average_by_agent": [0.3, 0.3, 0.4], "inference_seconds": 0.01},
        },
        "identical_terminal_answers": 0, "runtime_failures": [], "learned_weight_changes": [],
    }
    path = _report(config, summary)
    text = path.read_text(encoding="utf-8")
    assert path.exists() and STATUS_MOCK in text
    assert "API attempts" in text and "successful/harmful cases" in text
