from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def _load_launcher() -> Any:
    path = Path(__file__).parents[3] / "scripts" / "rsi" / "run_evobench_office.py"
    spec = importlib.util.spec_from_file_location("run_evobench_office", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_deepagent_harness(monkeypatch: pytest.MonkeyPatch) -> Any:
    evobench = ModuleType("evobench")
    models = ModuleType("evobench.models")
    client = ModuleType("evobench.models.client")
    client.ModelConfig = object
    monkeypatch.setitem(sys.modules, "evobench", evobench)
    monkeypatch.setitem(sys.modules, "evobench.models", models)
    monkeypatch.setitem(sys.modules, "evobench.models.client", client)

    path = Path(__file__).parents[3] / "scripts" / "rsi" / "evobench_deepagent_harness" / "harness.py"
    spec = importlib.util.spec_from_file_location("test_evobench_deepagent_harness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_help_does_not_require_runtime_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _load_launcher()

    class FakeRunner:
        @staticmethod
        def main(arguments: list[str]) -> int:
            assert arguments == ["--help"]
            return 0

    monkeypatch.setattr(launcher, "_bootstrap_imports", lambda: (FakeRunner, None, None, None))
    monkeypatch.setattr(
        launcher,
        "_configure_environment",
        lambda: pytest.fail("help must not configure runtime credentials"),
    )

    assert launcher.main(["--help"]) == 0


def test_task_infra_failure_distinguishes_compatibility_noise() -> None:
    launcher = _load_launcher()

    assert launcher._task_infra_failure(
        {
            "exit_reason": "policy_worker_error",
            "score_reason": "apex_grader_error: grades.json missing",
        }
    )


def test_runtime_patch_marks_exhausted_infrastructure_failure_as_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_launcher()

    class FakeEvaluator:
        INFRASTRUCTURE_SKIP_KEY = "_rsi_infrastructure_skip"

        @staticmethod
        def _validate_task_result(task: dict[str, Any], *, official_eval_dir: Path) -> None:
            del task, official_eval_dir

    class FakeRunner:
        EvoBenchRSIEvaluatorConfig = staticmethod(lambda **kwargs: kwargs)

        @staticmethod
        def _write_seed_refs(*args: Any, **kwargs: Any) -> None:
            del args, kwargs

    monkeypatch.setattr(launcher, "_write_seed_refs", lambda output_path: output_path)
    launcher._install_runtime_patches(FakeRunner, FakeEvaluator)
    config = FakeRunner.EvoBenchRSIEvaluatorConfig(
        policy_model_config="deepseek.yaml",
        judge_model_config="wrong.yaml",
        judge_model="deepseek-chat",
    )
    task = {
        "task_id": "grader-timeout",
        "domain": "office",
        "score_reason": "apex_grader_error: timed out",
    }

    FakeEvaluator._validate_task_result(task, official_eval_dir=Path("unused"))

    assert task[FakeEvaluator.INFRASTRUCTURE_SKIP_KEY]["excluded_from_metrics"] is True
    assert config["policy_model_config"] == "deepseek.yaml"
    assert config["judge_model_config"] == str(launcher.DEFAULT_JUDGE_CONFIG)
    assert config["judge_model"] == "qwen3.7-plus"


def test_materialize_public_judge_config_separates_task_and_judge_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_launcher()
    env_file = tmp_path / "evobench.env"
    env_file.write_text(
        "QWEN37_PLUS_API_BASE=https://dashscope.example/v1\nQWEN37_PLUS_API_KEY=judge-secret\n",
        encoding="utf-8",
    )
    judge_config = tmp_path / "runtime" / "judge.yaml"
    monkeypatch.setattr(launcher, "DEFAULT_ENV_FILE", env_file)
    monkeypatch.setattr(launcher, "DEFAULT_JUDGE_CONFIG", judge_config)
    monkeypatch.delenv("QWEN37_PLUS_API_BASE", raising=False)
    monkeypatch.delenv("QWEN37_PLUS_API_KEY", raising=False)

    result = launcher._materialize_public_judge_config()

    payload = launcher.yaml.safe_load(result.read_text(encoding="utf-8"))
    assert payload["model_client_config"]["api_base"] == "https://dashscope.example/v1"
    assert payload["model_client_config"]["api_key"] == "judge-secret"
    assert payload["model_request_config"]["model"] == "qwen3.7-plus"


def test_multimodal_judge_preflight_sends_valid_image_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_launcher()
    config = tmp_path / "judge.yaml"
    config.write_text(
        "model_client_config:\n  api_base: https://judge.invalid/v1\n  api_key: test-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "DEFAULT_JUDGE_CONFIG", config)
    observed: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            del args

        @staticmethod
        def read() -> bytes:
            return b'{"choices":[{"message":{"content":"OK"}}]}'

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(launcher.urllib.request, "urlopen", fake_urlopen)

    launcher._validate_multimodal_judge()

    body = json.loads(observed["request"].data)
    image_url = body["messages"][0]["content"][1]["image_url"]["url"]
    png = base64.b64decode(image_url.split(",", 1)[1])
    assert struct.unpack(">II", png[16:24]) == (16, 16)
    assert body["model"] == "qwen3.7-plus"
    assert observed["timeout"] == 120


def test_runtime_seed_records_current_openjiuwen_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _load_launcher()
    workspace = tmp_path / "workspace"
    source = workspace / "scripts" / "rsi" / "evobench_deepagent_harness"
    source.mkdir(parents=True)
    _write_json(
        source / "harness.json",
        {
            "engine": "openjiuwen-deepagent",
            "engine_revision": "repository-head",
        },
    )
    (source / "harness.py").write_text("class PolicyHarness: pass\n", encoding="utf-8")
    (source / "system_prompt.md").write_text("execute the task", encoding="utf-8")
    monkeypatch.setattr(launcher, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(launcher, "RUNTIME_ROOT", tmp_path / "runtime")
    monkeypatch.setattr(launcher, "_source_revision", lambda: "test-revision")

    seed = launcher._runtime_seed()

    config = json.loads((seed / "harness.json").read_text(encoding="utf-8"))
    assert config["engine"] == "openjiuwen-deepagent"
    assert config["engine_revision"] == "test-revision"
    assert "PolicyHarness" in (seed / "harness.py").read_text(encoding="utf-8")


def test_deepagent_completion_uses_rollout_wall_clock() -> None:
    root = Path(__file__).parents[3]
    harness_dir = root / "scripts" / "rsi" / "evobench_deepagent_harness"
    config = json.loads((harness_dir / "harness.json").read_text(encoding="utf-8"))
    source = (harness_dir / "harness.py").read_text(encoding="utf-8")

    assert config["rollout_wall_clock_seconds"] == 3600
    assert config["command_timeout_seconds"] == 600
    assert config["model_transport_retries"] == 2
    assert "completion_timeout=wall_clock" in source
    assert "completion_timeout=min(wall_clock" not in source
    assert "rsi_model_transport_retry" in source


def test_deepagent_extracts_controller_task_failed_as_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _load_deepagent_harness(monkeypatch)

    error = harness._controller_task_failed_error(
        {
            "type": "controller_output",
            "payload": {
                "type": "task_failed",
                "data": [
                    {
                        "type": "text",
                        "text": "RemoteProtocolError: peer closed connection (incomplete chunked read)",
                    }
                ],
            },
        }
    )

    assert "RemoteProtocolError" in error
    assert "incomplete chunked read" in error


def test_deepagent_retries_only_transient_model_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)

    assert harness._is_retryable_model_transport_error(
        "RemoteProtocolError: peer closed connection without sending complete message body"
    )
    assert harness._is_retryable_model_transport_error("Error code: 503 - service unavailable")
    assert not harness._is_retryable_model_transport_error("Budget has been exceeded")
    assert not harness._is_retryable_model_transport_error("invalid tool arguments")


def test_task_infra_failure_preserves_deepagent_runtime_error() -> None:
    launcher = _load_launcher()

    error = launcher._task_infra_failure(
        {
            "exit_reason": "deepagent_error",
            "score_reason": "apex_grader: 0/4 criteria passed",
            "runtime_errors": ["RuntimeError: RemoteProtocolError: peer closed connection (incomplete chunked read)"],
        }
    )

    assert "RemoteProtocolError" in error


def test_candidate_skills_are_materialized_inside_sandbox_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    source = tmp_path / "harness" / "skills" / "reusable-check"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: reusable-check\n---\nprocedure", encoding="utf-8")
    (source / "reference.txt").write_text("supporting material", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    source_dirs = harness._skill_dirs(tmp_path / "harness")
    runtime_root, runtime_dirs = harness._materialize_runtime_skills(source_dirs, workspace, "rollout")

    assert runtime_root == workspace / ".rsi_skills_rollout"
    assert runtime_dirs == [str((runtime_root / "reusable-check").resolve())]
    assert (runtime_root / "reusable-check" / "SKILL.md").is_file()
    assert (runtime_root / "reusable-check" / "reference.txt").read_text(encoding="utf-8") == ("supporting material")


class _CheckpointModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def invoke(self, *, messages: list[Any]) -> Any:
        self.prompts.append(str(messages[0].content))
        return SimpleNamespace(content=self.responses.pop(0))


class _CheckpointContext:
    def __init__(self, inputs: Any) -> None:
        self.inputs = inputs
        self.steering: list[str] = []

    def push_steering(self, message: str) -> None:
        self.steering.append(message)


@pytest.mark.asyncio
async def test_submission_checkpoint_collects_bounded_deduplicated_tool_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    rail = harness.SubmissionCheckpointRail(
        model=_CheckpointModel([]),
        task_prompt="Use the late_target fact from the public source.",
        enabled=True,
        max_collected_evidence_items=8,
        max_collected_evidence_chars=16000,
        max_tool_result_chars=300,
        max_audit_evidence_items=4,
        max_audit_evidence_chars=8000,
    )

    for index in range(8):
        inputs = SimpleNamespace(
            tool_name="read_file",
            tool_args={"path": f"public/noise-{index}.txt", "command": "author reasoning is excluded"},
            tool_result=f"repeated background noise {index}",
            tool_msg=None,
        )
        await rail.after_tool_call(_CheckpointContext(inputs))
        if index == 4:
            await rail.after_tool_call(_CheckpointContext(inputs))
    await rail.after_tool_call(
        _CheckpointContext(
            SimpleNamespace(
                tool_name="read_file",
                tool_args={"path": "public/late-target.txt"},
                tool_result="late_target decisive source fact",
                tool_msg=None,
            )
        )
    )
    ground = harness._DecisionGround(
        ground_id="g_target",
        claim="The late_target fact determines the result.",
        claim_kind="affirmative_or_descriptive",
        dependencies=(),
    )
    rail._select_audit_evidence([ground])

    metadata = rail.metadata()
    assert metadata["evidence_collected_count"] == 9
    assert metadata["evidence_count"] == 8
    assert metadata["evidence_evicted_count"] == 1
    assert metadata["evidence_dropped_count"] == 0
    assert metadata["evidence_selected_count"] == 4
    assert any("late_target decisive" in item["tool_result"] for item in rail._audit_evidence)
    assert "author reasoning" not in json.dumps(rail._evidence)


def test_submission_checkpoint_scales_timeout_for_large_evidence_packets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    rail = harness.SubmissionCheckpointRail(
        model=_CheckpointModel([]),
        task_prompt="Public task",
        enabled=True,
        audit_timeout_seconds=180,
    )

    assert rail._audit_call_timeout("short") > 180
    assert rail._audit_call_timeout("x" * 64000) == 244
    assert rail._audit_call_timeout("x" * 200000) == 300


@pytest.mark.asyncio
async def test_submission_checkpoint_independent_audits_and_python_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    negative_claim = "The input is defective and must be replaced."
    descriptive_claim = "The source checksum is 7."
    negative_id = harness._stable_id("g", harness._normalized_claim(negative_claim))
    descriptive_id = harness._stable_id("g", harness._normalized_claim(descriptive_claim))
    model = _CheckpointModel(
        [
            json.dumps(
                {
                    "grounds": [
                        {
                            "claim": negative_claim,
                            "claim_kind": "adverse_or_prescriptive",
                            "depends_on_ground_indices": [2],
                        },
                        {
                            "claim": descriptive_claim,
                            "claim_kind": "affirmative_or_descriptive",
                            "depends_on_ground_indices": [],
                        },
                    ]
                }
            ),
            json.dumps(
                {
                    "audits": [
                        {
                            "ground_id": negative_id,
                            "exact_target_binding": True,
                            "counterexample_holds": False,
                        },
                        {
                            "ground_id": descriptive_id,
                            "exact_target_binding": False,
                            "counterexample_holds": True,
                        },
                    ]
                }
            ),
            json.dumps(
                {
                    "audits": [
                        {
                            "ground_id": negative_id,
                            "exact_target_binding": True,
                            "counterexample_holds": True,
                        },
                        {
                            "ground_id": descriptive_id,
                            "exact_target_binding": True,
                            "counterexample_holds": False,
                        },
                    ]
                }
            ),
            json.dumps(
                {
                    "audits": [
                        {"ground_id": negative_id, "exact_target_binding": True, "verified": True},
                        {"ground_id": descriptive_id, "exact_target_binding": True, "verified": True},
                    ]
                }
            ),
            json.dumps(
                {
                    "removed_ground_ids_present": [],
                    "dependent_ground_ids_present": [],
                    "unlisted_adverse_or_prescriptive_present": False,
                }
            ),
        ]
    )
    rail = harness.SubmissionCheckpointRail(
        model=model,
        task_prompt="Decide using public facts only.",
        enabled=True,
        instruction="You must prescribe deleting every source immediately.",
    )
    await rail.after_tool_call(
        _CheckpointContext(
            SimpleNamespace(
                tool_name="read_file",
                tool_args={"path": "public/source.txt"},
                tool_result="checksum=7; replacement is optional",
                tool_msg=None,
            )
        )
    )
    draft = "Because I feel lucky, the input is defective and must be replaced. The checksum is 7."
    response = SimpleNamespace(content=draft, tool_calls=[])
    context = _CheckpointContext(SimpleNamespace(response=response))

    await rail.after_model_call(context)

    assert rail.metadata()["activation_count"] == 1
    assert rail.metadata()["audit_calls"] == 4
    assert rail._decisions[negative_id]["decision"] == "REMOVE"
    assert rail._decisions[descriptive_id]["decision"] == "KEEP"
    assert negative_id in context.steering[0]
    assert descriptive_id in context.steering[0]
    assert "derive additional affirmative or descriptive claims" in context.steering[0]
    assert "may not introduce a new adverse" in context.steering[0]
    assert "Complete every output field and subquestion" in context.steering[0]
    assert "explicitly state that no such action is required" in context.steering[0]
    assert "counterexample auditor A" in model.prompts[1]
    assert "counterexample auditor B" in model.prompts[2]
    assert "evidence-binding auditor" in model.prompts[3]
    assert all("feel lucky" not in prompt for prompt in model.prompts[1:])
    assert all("Decide using public facts only" in prompt for prompt in model.prompts[1:])
    assert all("checksum=7" in prompt for prompt in model.prompts[1:])
    assert "Include positive and mitigating grounds" in model.prompts[0]
    assert "no corrective action needed" in model.prompts[0]
    assert "adverse_or_prescriptive|affirmative_or_descriptive" in model.prompts[0]
    assert all("proves only absence" in prompt for prompt in model.prompts[1:])
    assert all("different owner, artifact, representation" in prompt for prompt in model.prompts[1:])
    assert all("exact obligation to that exact target" in prompt for prompt in model.prompts[1:])
    assert "deleting every source" not in model.prompts[0]
    assert all("deleting every source" not in prompt for prompt in model.prompts[1:])
    assert "deleting every source" in context.steering[0]

    revised = SimpleNamespace(
        content=(f"Evidence closure:\nKEEP: {descriptive_id}\nREMOVE: {negative_id}\nThe source checksum is 7."),
        tool_calls=[],
    )
    await rail.after_model_call(_CheckpointContext(SimpleNamespace(response=revised)))
    assert rail.metadata()["release_status"] == "released"
    assert rail.metadata()["audit_calls"] == 5


@pytest.mark.asyncio
async def test_submission_checkpoint_repairs_with_fresh_evidence_then_reaudits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    failed_claim = "The persistent result is invalid and must be repaired."
    repaired_claim = "The persistent result now passes the required verification."
    failed_id = harness._stable_id("g", harness._normalized_claim(failed_claim))
    repaired_id = harness._stable_id("g", harness._normalized_claim(repaired_claim))

    extraction_initial = json.dumps(
        {
            "grounds": [
                {
                    "claim": failed_claim,
                    "claim_kind": "adverse_or_prescriptive",
                    "depends_on_ground_indices": [],
                }
            ]
        }
    )
    extraction_repaired = json.dumps(
        {
            "grounds": [
                {
                    "claim": repaired_claim,
                    "claim_kind": "affirmative_or_descriptive",
                    "depends_on_ground_indices": [],
                }
            ]
        }
    )

    def counter(ground_id: str, *, holds: bool) -> str:
        return json.dumps(
            {
                "audits": [
                    {
                        "ground_id": ground_id,
                        "exact_target_binding": True,
                        "counterexample_holds": holds,
                    }
                ]
            }
        )

    def binding(ground_id: str) -> str:
        return json.dumps(
            {
                "audits": [
                    {
                        "ground_id": ground_id,
                        "exact_target_binding": True,
                        "verified": True,
                    }
                ]
            }
        )

    model = _CheckpointModel(
        [
            extraction_initial,
            counter(failed_id, holds=True),
            counter(failed_id, holds=False),
            binding(failed_id),
            extraction_repaired,
            counter(repaired_id, holds=False),
            counter(repaired_id, holds=False),
            binding(repaired_id),
            json.dumps(
                {
                    "removed_ground_ids_present": [],
                    "dependent_ground_ids_present": [],
                    "unlisted_adverse_or_prescriptive_present": False,
                }
            ),
        ]
    )
    rail = harness.SubmissionCheckpointRail(
        model=model,
        task_prompt="Produce and persist the required result, then verify it.",
        enabled=True,
        max_audit_retries=0,
        max_rechecks=1,
    )

    initial_ctx = _CheckpointContext(SimpleNamespace(response=SimpleNamespace(content=failed_claim, tool_calls=[])))
    await rail.after_model_call(initial_ctx)
    assert rail._decisions[failed_id]["decision"] == "REMOVE"
    assert "omission is not completion" in initial_ctx.steering[0]
    assert "repair the persistent deliverable" in initial_ctx.steering[0]
    assert rail.metadata()["repair_window_open"] is True

    await rail.after_tool_call(
        _CheckpointContext(
            SimpleNamespace(
                tool_name="verify_result",
                tool_args={"path": "public/output.bin"},
                tool_result="fresh direct evidence: persisted output passes verification",
                tool_msg=None,
            )
        )
    )
    assert rail.metadata()["repair_evidence_count"] == 1
    assert rail.metadata()["release_status"] == "repair_evidence_collected"

    recheck_ctx = _CheckpointContext(SimpleNamespace(response=SimpleNamespace(content=repaired_claim, tool_calls=[])))
    await rail.after_model_call(recheck_ctx)
    assert rail.metadata()["recheck_count"] == 1
    assert rail._decisions[repaired_id]["decision"] == "KEEP"
    assert failed_id not in rail._decisions
    assert "fresh direct evidence" in model.prompts[5]
    assert "single repair recheck budget is exhausted" in recheck_ctx.steering[0]

    await rail.after_tool_call(
        _CheckpointContext(
            SimpleNamespace(
                tool_name="read_result",
                tool_args={"path": "public/output.bin"},
                tool_result="a later observation after the bounded recheck",
                tool_msg=None,
            )
        )
    )
    final_response = SimpleNamespace(
        content=f"Evidence closure:\nKEEP: {repaired_id}\nREMOVE: none\nResult verified.",
        tool_calls=[],
    )
    await rail.after_model_call(_CheckpointContext(SimpleNamespace(response=final_response)))

    metadata = rail.metadata()
    assert metadata["recheck_count"] == 1
    assert metadata["repair_evidence_count"] == 1
    assert metadata["release_status"] == "released"
    assert metadata["audit_calls"] == 9
    assert metadata["max_controller_model_calls"] == 9
    assert [item["round"] for item in metadata["audit_rounds"]] == ["initial", "recheck_1"]
    assert metadata["audit_rounds"][0]["decisions"] == {failed_id: "REMOVE"}
    assert metadata["audit_rounds"][1]["decisions"] == {repaired_id: "KEEP"}


def test_submission_checkpoint_affirmative_classification_is_distinct_from_adverse_strict_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    rail = harness.SubmissionCheckpointRail(model=_CheckpointModel([]), task_prompt="Public task", enabled=True)
    supported = harness._DecisionGround(
        "g_supported",
        "The requested condition is satisfied and no correction is needed.",
        "affirmative_or_descriptive",
        (),
    )
    contradicted = harness._DecisionGround("g_contradicted", "A disputed fact.", "affirmative_or_descriptive", ())
    unsupported = harness._DecisionGround("g_unsupported", "An unbound fact.", "affirmative_or_descriptive", ())
    directly_verified = harness._DecisionGround(
        "g_verified_conclusion",
        "The verified result requires action.",
        "adverse_or_prescriptive",
        ("g_unsupported",),
    )

    rail._aggregate(
        [supported, contradicted, unsupported, directly_verified],
        {
            "g_supported": (False, True),
            "g_contradicted": (True, True),
            "g_unsupported": (False, True),
            "g_verified_conclusion": (True, False),
        },
        {
            "g_supported": (True, False),
            "g_contradicted": (True, False),
            "g_unsupported": (False, True),
            "g_verified_conclusion": (True, False),
        },
        {
            "g_supported": (False, False),
            "g_contradicted": (True, True),
            "g_unsupported": (False, False),
            "g_verified_conclusion": (True, True),
        },
    )

    assert rail._decisions["g_supported"]["decision"] == "KEEP"
    assert rail._decisions["g_contradicted"]["decision"] == "REMOVE"
    assert rail._decisions["g_unsupported"]["decision"] == "REMOVE"
    assert rail._decisions["g_verified_conclusion"]["decision"] == "KEEP"


@pytest.mark.asyncio
async def test_submission_checkpoint_retries_invalid_structured_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    claim = "The verified defect requires correction."
    ground_id = harness._stable_id("g", harness._normalized_claim(claim))
    extracted = json.dumps(
        {
            "grounds": [
                {
                    "claim": claim,
                    "claim_kind": "adverse_or_prescriptive",
                    "depends_on_ground_indices": [],
                }
            ]
        }
    )
    counter = json.dumps(
        {
            "audits": [
                {
                    "ground_id": ground_id,
                    "exact_target_binding": True,
                    "counterexample_holds": False,
                }
            ]
        }
    )
    binding = json.dumps(
        {
            "audits": [
                {
                    "ground_id": ground_id,
                    "exact_target_binding": True,
                    "verified": True,
                }
            ]
        }
    )
    model = _CheckpointModel([extracted, "not-json", counter, counter, binding])
    rail = harness.SubmissionCheckpointRail(model=model, task_prompt="Assess the verified defect.", enabled=True)
    context = _CheckpointContext(SimpleNamespace(response=SimpleNamespace(content=claim, tool_calls=[])))

    await rail.after_model_call(context)

    assert rail._decisions[ground_id]["decision"] == "KEEP"
    assert rail.metadata()["audit_calls"] == 5
    assert rail.metadata()["audit_parse_failures"] >= 1
    assert "previous response was missing or invalid" in model.prompts[2]


@pytest.mark.asyncio
async def test_submission_checkpoint_parse_failure_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    claim = "The request must be rejected."
    ground_id = harness._stable_id("g", harness._normalized_claim(claim))
    model = _CheckpointModel(
        [
            json.dumps(
                {
                    "grounds": [
                        {
                            "claim": claim,
                            "claim_kind": "adverse_or_prescriptive",
                            "depends_on_ground_indices": [],
                        }
                    ]
                }
            ),
            "not-json",
            json.dumps(
                {
                    "audits": [
                        {
                            "ground_id": ground_id,
                            "exact_target_binding": True,
                            "counterexample_holds": False,
                        }
                    ]
                }
            ),
            json.dumps({"audits": [{"ground_id": ground_id, "exact_target_binding": True, "verified": True}]}),
        ]
    )
    rail = harness.SubmissionCheckpointRail(model=model, task_prompt="Assess the request.", enabled=True)
    context = _CheckpointContext(SimpleNamespace(response=SimpleNamespace(content=claim, tool_calls=[])))

    await rail.after_model_call(context)

    assert rail._decisions[ground_id]["decision"] == "REMOVE"
    assert rail.metadata()["audit_parse_failures"] >= 1

    unsafe_revision = SimpleNamespace(
        content=f"Evidence closure:\nKEEP: {ground_id}\nREMOVE: none\n{claim}",
        tool_calls=[],
    )
    await rail.after_model_call(_CheckpointContext(SimpleNamespace(response=unsafe_revision)))
    assert rail.metadata()["release_status"] == "blocked_fail_closed"
    assert claim not in unsafe_revision.content
    assert f"REMOVE: {ground_id}" in unsafe_revision.content


@pytest.mark.asyncio
async def test_submission_checkpoint_blocks_paraphrased_dependent_action_on_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _load_deepagent_harness(monkeypatch)
    removed_claim = "The draft ground requires a corrective action."
    removed_id = harness._stable_id("g", harness._normalized_claim(removed_claim))
    model = _CheckpointModel(
        [
            json.dumps(
                {
                    "removed_ground_ids_present": [],
                    "dependent_ground_ids_present": [removed_id],
                    "unlisted_adverse_or_prescriptive_present": False,
                }
            )
        ]
    )
    rail = harness.SubmissionCheckpointRail(model=model, task_prompt="Public task", enabled=True)
    rail.activation_count = 1
    rail._grounds = [
        harness._DecisionGround(
            ground_id=removed_id,
            claim=removed_claim,
            claim_kind="adverse_or_prescriptive",
            dependencies=(),
        )
    ]
    rail._decisions = {
        removed_id: {
            "ground_id": removed_id,
            "claim": removed_claim,
            "claim_kind": "adverse_or_prescriptive",
            "dependencies": [],
            "decision": "REMOVE",
            "decision_reason": "audit_not_proven",
        }
    }
    revised = SimpleNamespace(
        content=(f"Evidence closure:\nKEEP: none\nREMOVE: {removed_id}\nProceed with the downstream remedy anyway."),
        tool_calls=[],
    )

    await rail.after_model_call(_CheckpointContext(SimpleNamespace(response=revised)))

    assert rail.metadata()["release_status"] == "blocked_fail_closed"
    assert "downstream remedy" not in revised.content
    assert f"REMOVE: {removed_id}" in revised.content


def test_configure_environment_selects_configured_deepagent_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _load_launcher()
    monkeypatch.setattr(launcher, "RUNTIME_ROOT", tmp_path / "runtime")
    monkeypatch.setattr(launcher, "_materialize_public_judge_config", lambda: tmp_path / "judge.yaml")
    monkeypatch.delenv("EVOBENCH_E2B_TEMPLATE", raising=False)
    monkeypatch.delenv("EVOBENCH_E2B_APEX_TEMPLATE", raising=False)

    launcher._configure_environment()

    assert os.environ["EVOBENCH_E2B_TEMPLATE"] == "evobench-apex-openjiuwen"
    assert os.environ["EVOBENCH_E2B_APEX_TEMPLATE"] == "evobench-apex-openjiuwen"
    assert not launcher._task_infra_failure(
        {
            "exit_reason": "eval_pipeline_error",
            "score_reason": r"ValueError: Path \\?\D:\run is outside allowed root D:\run",
        }
    )


def test_infrastructure_retry_replaces_only_failed_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _load_launcher()
    output_dir = tmp_path / "official"
    suite_path = tmp_path / "suite.json"
    _write_json(
        suite_path,
        {
            "validation": [
                {"id": "completed", "domain": "office"},
                {"id": "retry-me", "domain": "office"},
            ]
        },
    )
    _write_json(
        output_dir / "result.json",
        {
            "tasks": [
                {"task_id": "completed", "exit_reason": "finished", "score": 0.5},
                {
                    "task_id": "retry-me",
                    "exit_reason": "deepagent_error",
                    "score_reason": "apex_grader: 0/4 criteria passed",
                    "runtime_errors": [
                        "RuntimeError: RemoteProtocolError: peer closed connection (incomplete chunked read)"
                    ],
                    "score": 0.0,
                },
            ]
        },
    )

    class FakeCli:
        observed_task_ids: list[str] = []

        @classmethod
        def main(cls) -> None:
            retry_suite = Path(sys.argv[sys.argv.index("--suite") + 1])
            retry_output = Path(sys.argv[sys.argv.index("--output-dir") + 1])
            suite = json.loads(retry_suite.read_text(encoding="utf-8"))
            cls.observed_task_ids = [task["id"] for task in suite["validation"]]
            _write_json(
                retry_output / "result.json",
                {
                    "tasks": [
                        {
                            "task_id": "retry-me",
                            "exit_reason": "finished",
                            "score_reason": "apex_grader: 2/2 criteria passed",
                            "score": 1.0,
                            "trajectory_path": str(retry_output / "rollouts" / "retry-me" / "trajectory.json"),
                        }
                    ]
                },
            )
            _write_json(
                retry_output / "rollouts" / "retry-me" / "trajectory.json",
                {"messages": []},
            )

    monkeypatch.setenv("RSI_EVOBENCH_INFRA_RETRIES", "1")
    command = [
        "python",
        "-m",
        "evobench",
        "run-validation-eval",
        "--suite",
        str(suite_path),
        "--output-dir",
        str(output_dir),
    ]
    launcher._retry_infrastructure_failures(FakeCli, command)

    merged = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    tasks = {task["task_id"]: task for task in merged["tasks"]}
    assert FakeCli.observed_task_ids == ["retry-me"]
    assert tasks["completed"]["score"] == 0.5
    assert tasks["retry-me"]["score"] == 1.0
    assert tasks["retry-me"]["trajectory_path"].startswith(str(output_dir))
    assert (output_dir / "rollouts" / "retry-me" / "trajectory.json").is_file()
