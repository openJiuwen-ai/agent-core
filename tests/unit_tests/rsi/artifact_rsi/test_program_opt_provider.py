# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the ScienceDiscovery-backed program optimization provider.

The interesting surface is not the protocol -- `isinstance` settles that -- but
the three translations the provider performs: a platform model reference into a
callable endpoint, an isolation probe into a refusal, and nine search events
into the contract's three. Each is tested against the thing it actually talks
to (a real `model_config` file, the vendored event constructors) rather than a
restatement of the provider's own shape, so a change on either side breaks the
test rather than passing it.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Literal, get_type_hints

import pytest

from openjiuwen.rsi.artifact_rsi.program_opt import events
from openjiuwen.rsi.artifact_rsi.program_opt import state as state_module
from openjiuwen.rsi.artifact_rsi.program_opt.provider import ProgramArtifactProvider
from openjiuwen.rsi.artifact_rsi.program_opt.runtime import (
    DEFAULT_MAX_TOKENS_PER_CALL,
    ModelConfigError,
    SandboxUnavailable,
    load_model_endpoint,
    require_sandbox,
)
from openjiuwen.rsi.artifact_rsi.program_opt.sandbox import (
    SandboxCapability,
)
from openjiuwen.rsi.artifact_rsi.program_opt.state import ProgramRunState
from openjiuwen.rsi.artifact_rsi.provider import ArtifactProvider
from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.events import EventNode, EventProgress, EventStatus

SEED = """
def train_and_predict(train, test):
    return [0.0 for _ in test]
"""


@pytest.fixture(autouse=True)
def _isolated_run_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own task-id -> run_dir map and no ambient root."""
    monkeypatch.setattr(state_module, "_DIRECTORIES", {})
    monkeypatch.delenv("SCIENCE_AGENT_RSI_RUNS", raising=False)


def _model_config(tmp_path: Path, **client: object) -> str:
    values = {"api_base": "https://models.invalid/v1", "api_key": "sk-inline"}
    values.update(client)
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"model_client_config": values}), encoding="utf-8")
    return str(path)


def _request(tmp_path: Path, **overrides: object) -> ArtifactEngineRequest:
    seed = tmp_path / "seed.py"
    seed.write_text(SEED, encoding="utf-8")
    values: dict[str, object] = {
        "task_id": "task-001",
        "run_dir": str(tmp_path / "run"),
        "artifact_path": str(seed),
        "model_config": _model_config(tmp_path),
        "max_iterations": 3,
        "optimization_instruction": None,
    }
    values.update(overrides)
    return ArtifactEngineRequest(**values)  # type: ignore[arg-type]


def _scorecard(run_dir: Path, **extra: object) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    card: dict[str, object] = {
        "scorecard": {"aggregate": "weighted_sum", "criteria": [], "constraints": []},
        "hash": "sha256:test",
        "statement": "bring the error down",
        "script": "def evaluate(): ...",
    }
    card.update(extra)
    (run_dir / "scorecard.json").write_text(json.dumps(card), encoding="utf-8")


# -- the protocol ---------------------------------------------------------------


def test_the_provider_satisfies_the_structural_contract() -> None:
    """`ArtifactProvider` is the protocol AgentServer routes against, and it is
    the only one: this class is the program contract rather than a second
    implementation of a protocol restating it."""
    provider = ProgramArtifactProvider()

    assert isinstance(provider, ArtifactProvider)
    assert provider.artifact_type == "program"
    assert get_type_hints(ProgramArtifactProvider)["artifact_type"] == Literal["program"]


# -- the model reference --------------------------------------------------------


def test_a_v1_api_base_yields_one_v1(tmp_path: Path) -> None:
    """`api_base` is conventionally the `/v1` root, and appending `/v1` again is
    a 404 that reads like a broken model rather than a broken URL."""
    endpoint = load_model_endpoint(_model_config(tmp_path))

    assert endpoint["endpoint"] == "https://models.invalid/v1/chat/completions"


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://m.invalid", "https://m.invalid/v1/chat/completions"),
        ("https://m.invalid/v1", "https://m.invalid/v1/chat/completions"),
        ("https://m.invalid/v1/", "https://m.invalid/v1/chat/completions"),
        ("https://m.invalid/v1/chat/completions", "https://m.invalid/v1/chat/completions"),
    ],
)
def test_every_spelling_of_api_base_reaches_the_same_url(
    tmp_path: Path, base: str, expected: str
) -> None:
    """All four appear in real config files; they must not mean four endpoints."""
    endpoint = load_model_endpoint(_model_config(tmp_path, api_base=base))

    assert endpoint["endpoint"] == expected


def test_the_key_can_live_in_the_environment_rather_than_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROGRAM_OPT_TEST_KEY", "sk-from-env")

    endpoint = load_model_endpoint(
        _model_config(tmp_path, api_key="${PROGRAM_OPT_TEST_KEY}")
    )

    assert endpoint["token"] == "sk-from-env"
    assert "sk-from-env" not in (tmp_path / "model.json").read_text(encoding="utf-8")


def test_a_missing_model_config_is_a_refusal_not_a_default(tmp_path: Path) -> None:
    with pytest.raises(ModelConfigError):
        load_model_endpoint("")
    with pytest.raises(ModelConfigError):
        load_model_endpoint(str(tmp_path / "absent.yaml"))


def test_a_config_without_api_base_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"model_client_config": {"api_key": "sk"}}), encoding="utf-8")

    with pytest.raises(ModelConfigError, match="api_base"):
        load_model_endpoint(str(path))


# -- isolation ------------------------------------------------------------------


def test_no_isolation_backend_means_no_run() -> None:
    """The contract has no sandbox field, so this refusal is the only thing
    standing between model-written code and the task's own model key."""
    with pytest.raises(SandboxUnavailable):
        require_sandbox(override="none")


def test_an_override_is_taken_over_the_probe() -> None:
    assert require_sandbox(override="bwrap") == SandboxCapability(backend="bwrap")


def test_the_probe_answers_when_nothing_is_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.runtime.detect_local_capability",
        lambda: SandboxCapability(backend="seatbelt"),
    )

    assert require_sandbox().backend == "seatbelt"


# -- validate_input -------------------------------------------------------------


def test_a_program_the_gate_would_reject_is_refused_at_the_form(tmp_path: Path) -> None:
    """The same AST gate every candidate passes. A seed that fails it would fail
    every expansion identically, and finding that out after paying for three
    model calls is the outcome this check exists to prevent."""
    path = tmp_path / "seed.py"
    path.write_text("def something_else():\n    return 1\n", encoding="utf-8")

    result = ProgramArtifactProvider().validate_input(str(path))

    assert result.valid is False
    assert [error["code"] for error in result.errors] == ["ARTIFACT_REJECTED_BY_GATE"]


def test_a_usable_seed_validates(tmp_path: Path) -> None:
    path = tmp_path / "seed.py"
    path.write_text(SEED, encoding="utf-8")

    assert ProgramArtifactProvider().validate_input(str(path)).valid is True


@pytest.mark.parametrize(
    ("artifact_path", "code"),
    [(None, "ARTIFACT_PATH_REQUIRED"), ("", "ARTIFACT_PATH_REQUIRED")],
)
def test_no_program_at_all_is_named_as_such(artifact_path: str | None, code: str) -> None:
    result = ProgramArtifactProvider().validate_input(artifact_path)

    assert [error["code"] for error in result.errors] == [code]


def test_a_path_that_is_not_there_is_named_as_such(tmp_path: Path) -> None:
    result = ProgramArtifactProvider().validate_input(str(tmp_path / "absent.py"))

    assert [error["code"] for error in result.errors] == ["ARTIFACT_NOT_FOUND"]


# -- the run spec ---------------------------------------------------------------


def test_a_run_without_a_scorecard_is_refused_rather_than_scored_by_a_guess(
    tmp_path: Path,
) -> None:
    provider = ProgramArtifactProvider()
    request = _request(tmp_path)
    Path(request.run_dir).mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="scorecard"):
        provider._spec_for(request, SandboxCapability(backend="bwrap"),
                           load_model_endpoint(request.model_config), resumed=False)


def test_the_token_ceiling_comes_from_the_model_config_not_the_engine_default(
    tmp_path: Path,
) -> None:
    """`RunSpec` defaults to 16k, and a reasoning model at 16k spends the whole
    allowance on hidden thinking and returns an empty reply -- which then reads
    as a model that cannot write code. The deployment's number has to win."""
    provider = ProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"),
                              load_model_endpoint(request.model_config), resumed=False)

    assert spec.max_tokens_per_call == DEFAULT_MAX_TOKENS_PER_CALL
    assert spec.max_tokens_per_call != 16_000


def test_the_starting_program_reaches_the_spec(tmp_path: Path) -> None:
    provider = ProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"),
                              load_model_endpoint(request.model_config), resumed=False)

    assert "train_and_predict" in spec.baseline_code
    assert spec.expansions == request.max_iterations
    assert spec.search_id == request.task_id


@pytest.mark.parametrize(
    "package",
    [
        "/etc/passwd",
        "https://evil.invalid/wheel.whl",
        "git+https://evil.invalid/repo",
        "--index-url=https://evil.invalid/simple",
        "requests; python_version<'3'",
        "./local-dir",
    ],
)
def test_packages_takes_names_a_reviewer_can_recognise_and_nothing_else(
    tmp_path: Path, package: str
) -> None:
    """`packages` is written by a model, so readability is the whole security
    boundary: a name is checkable by eye, a path or a URL or a pip option is
    not, however plausible the surrounding text makes it look."""
    provider = ProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), packages=[package])

    with pytest.raises(ValueError, match="bare distribution names"):
        provider._spec_for(request, SandboxCapability(backend="bwrap"),
                           load_model_endpoint(request.model_config), resumed=False)


def test_an_ordinary_pinned_dependency_still_gets_through(tmp_path: Path) -> None:
    provider = ProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), packages=["xgboost", "scikit-learn==1.5.0"])

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"),
                              load_model_endpoint(request.model_config), resumed=False)

    assert spec.packages == ("xgboost", "scikit-learn==1.5.0")


def test_resuming_continues_the_numbering_the_first_attempt_stopped_at(
    tmp_path: Path,
) -> None:
    """Without the previous tree a resumed run would append node 0 on top of a
    graph that already has one, leaving a single index holding two candidates."""
    provider = ProgramArtifactProvider()
    request = _request(tmp_path)
    run_dir = Path(request.run_dir)
    _scorecard(run_dir)
    (run_dir / "tree.json").write_text(
        json.dumps({
            "tree": [{"index": 0, "score": 0.1}, {"index": 1, "score": 0.4}],
            "baseline": {"rmse": 2.5},
            "tokens": 12_345,
        }),
        encoding="utf-8",
    )

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"),
                              load_model_endpoint(request.model_config), resumed=True)

    assert spec.resume_from_sequence == 2
    assert len(spec.resume_nodes) == 2
    assert spec.resume_baseline == {"rmse": 2.5}
    assert spec.resume_tokens == 12_345


# -- the projection -------------------------------------------------------------


def _state(tmp_path: Path, total: int = 3) -> ProgramRunState:
    return ProgramRunState(task_id="task-001", run_dir=tmp_path / "run", total_iterations=total)


def _absorb(run: ProgramRunState, event: dict[str, object]) -> list[object]:
    return list(run.absorb({"createdAt": "", "event": event, "sequence": 1}))


def test_the_root_arrives_as_one_complete_node(tmp_path: Path) -> None:
    run = _state(tmp_path)

    emitted = _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa", code_chars=120))

    assert len(emitted) == 1
    node = emitted[0]
    assert isinstance(node, EventNode)
    assert node.node.type == "root"
    assert node.node.adopted is True
    assert node.node.score == 0.25
    assert node.node.parent_id is None


def test_a_candidate_stays_silent_until_the_merger_has_ruled_on_it(
    tmp_path: Path,
) -> None:
    """`EventNode` carries a *complete* node by contract, and a candidate is not
    complete until it has been accepted or rejected. Announcing it at `expanded`
    would publish a node whose `adopted` is a placeholder."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))

    assert _absorb(run, events.expanded(1, 0, 1, 0.4, True, iteration=1,
                                       code_hash="sha256:bb",
                                       change_summary="swapped the solver")) == []
    assert _absorb(run, events.evaluated(1, 0.42, {"rmse": 1.9}, gate_score=0.42)) == []


def test_the_merge_publishes_the_node_and_the_progress_it_moved(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))
    _absorb(run, events.expanded(1, 0, 1, 0.4, True, iteration=1, code_hash="sha256:bb",
                                change_summary="swapped the solver"))
    _absorb(run, events.evaluated(1, 0.42, {"rmse": 1.9}, gate_score=0.42))

    emitted = _absorb(run, events.merged(1, True, "it scored better"))

    node, progress = emitted
    assert isinstance(node, EventNode) and isinstance(progress, EventProgress)
    # The three parts have been folded into one node: the parent from `expanded`,
    # the score from `evaluated`, the verdict from `merged`.
    assert node.node.parent_id == state_module.node_id_for("task-001", 0)
    assert node.node.score == 0.42
    assert node.node.type == "adopted"
    assert node.node.adopted is True
    assert progress.score == 0.42
    assert progress.baseline == 0.25
    assert progress.iteration == 1
    assert progress.total_iterations == 3


def test_a_rejected_candidate_is_still_published(tmp_path: Path) -> None:
    """The contract asks for the complete tree "including rejected branches" --
    a search that only reported its winners would show a straight line up."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))
    _absorb(run, events.expanded(1, 0, 1, 0.1, True, iteration=1, code_hash="sha256:bb"))
    _absorb(run, events.evaluated(1, 0.1, {"rmse": 4.0}))

    emitted = _absorb(run, events.merged(1, False, "it scored worse",
                                         category="constraint-violated"))

    node = emitted[0]
    assert isinstance(node, EventNode)
    assert node.node.adopted is False
    assert node.node.type == "rejected"
    assert node.node.failure_class == "gate_violation"
    # The best node did not move to the loser.
    assert run.best_node_id == state_module.node_id_for("task-001", 0)


def test_a_failed_candidate_carries_why_it_failed(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))

    _absorb(run, events.expanded(1, 0, 1, None, False, iteration=1,
                                error="ModuleNotFoundError: no module named 'torch'"))
    emitted = _absorb(run, events.merged(1, False, "it did not run"))

    node = emitted[0]
    assert isinstance(node, EventNode)
    assert node.node.failure_class == "import_error"
    assert "torch" in (node.node.reason or "")


def test_spend_is_reported_against_a_call_count_rather_than_zero(
    tmp_path: Path,
) -> None:
    """The search's `cost` event carries tokens only. A usage record showing
    hundreds of thousands of tokens against zero calls is a worse answer than a
    counted one, so the provider counts the calls it wraps."""
    run = _state(tmp_path)
    run.model_calls = 4

    emitted = _absorb(run, events.cost(412_000, 0))

    progress = emitted[0]
    assert isinstance(progress, EventProgress)
    assert progress.usage is not None
    assert progress.usage.tokens.output == 412_000
    assert progress.usage.call_count == 4


def test_a_short_run_says_so_in_the_report(tmp_path: Path) -> None:
    """20 planned and 18 made is the difference between a search that finished
    and one that lost two paid-for model calls; unexplained it reads as neither."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))

    _absorb(run, events.search_finished("completed", 0, 18, expansions_planned=20,
                                        stop_reason="two proposals went stale"))

    assert run.status == "completed"
    summary = run.to_report().summary or ""
    assert "planned 20 expansions, made 18" in summary
    assert "two proposals went stale" in summary


def test_a_stopped_search_is_terminated_not_completed(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))

    _absorb(run, events.search_finished("stopped", 0, 2))

    assert run.status == "terminated"


# -- durability -----------------------------------------------------------------


def test_a_node_can_be_read_back_the_moment_it_is_announced(tmp_path: Path) -> None:
    """Persist, then emit. A consumer told about a node it cannot then read is
    worse off than one told a moment later."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))
    _absorb(run, events.expanded(1, 0, 1, 0.4, True, iteration=1, code_hash="sha256:bb"))
    _absorb(run, events.evaluated(1, 0.42, {"rmse": 1.9}))
    _absorb(run, events.merged(1, True, "it scored better"))

    tree = state_module.read_tree_file("task-001")
    assert tree is not None
    assert [node.node_id for node in tree.nodes] == [
        state_module.node_id_for("task-001", 0),
        state_module.node_id_for("task-001", 1),
    ]
    assert tree.depth == 1
    assert state_module.read_state_file("task-001") is not None


def test_the_queries_answer_after_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`read_state` gets a task id and nothing else, so a provider that only
    remembered `run_dir` in memory would answer nothing after a restart."""
    runs = tmp_path / "runs"
    run = ProgramRunState(task_id="task-001", run_dir=runs / "task-001", total_iterations=3)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))

    monkeypatch.setattr(state_module, "_DIRECTORIES", {})
    monkeypatch.setenv("SCIENCE_AGENT_RSI_RUNS", str(runs))

    provider = ProgramArtifactProvider()
    assert provider.read_state("task-001").status == "created"
    assert provider.get_tree("task-001").nodes != []


def test_an_unknown_task_says_so_rather_than_pretending(tmp_path: Path) -> None:
    provider = ProgramArtifactProvider()

    state = provider.read_state("task-nowhere")

    assert state.error_code == "TASK_NOT_FOUND"
    assert provider.get_tree("task-nowhere").nodes == []
    assert provider.read_report("task-nowhere").best_node_id is None


def test_the_final_artifact_is_the_best_nodes(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:" + "a" * 64))
    _absorb(run, events.expanded(1, 0, 1, 0.4, True, iteration=1,
                                code_hash="sha256:" + "b" * 64))
    _absorb(run, events.evaluated(1, 0.42, {"rmse": 1.9}))
    _absorb(run, events.merged(1, True, "it scored better"))

    provider = ProgramArtifactProvider()
    final = provider.locate_artifact("task-001")

    assert final.node_id == state_module.node_id_for("task-001", 1)
    assert final.sha256 == "b" * 64


def test_an_artifact_from_another_task_is_not_served(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:" + "a" * 64))

    with pytest.raises(FileNotFoundError):
        ProgramArtifactProvider().locate_artifact("task-001", "A-program:other:dead")


# -- execution control ----------------------------------------------------------


def test_an_unusable_model_config_fails_the_task_rather_than_the_server(
    tmp_path: Path,
) -> None:
    """A bad reference is a task-level failure with a status the caller can see,
    not an exception AgentServer has to interpret."""
    provider = ProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path, model_config=str(tmp_path / "absent.yaml"))
    seen: list[object] = []

    async def sink(event: object) -> None:
        seen.append(event)

    result = asyncio.run(provider.run(request, sink))

    assert result.status == "failed"
    assert result.error_code == "MODELCONFIG"
    assert [e.status for e in seen if isinstance(e, EventStatus)] == ["failed"]
    # And it was written down, so `read_state` agrees with what was returned.
    assert provider.read_state(request.task_id).status == "failed"


def test_a_run_without_isolation_never_starts(tmp_path: Path) -> None:
    provider = ProgramArtifactProvider(sandbox_backend="none")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    result = asyncio.run(provider.run(request))

    assert result.status == "failed"
    assert result.error_code == "SANDBOXUNAVAILABLE"


def test_pause_is_declined_in_words_rather_than_faked(tmp_path: Path) -> None:
    result = asyncio.run(ProgramArtifactProvider().pause("task-001"))

    assert result.error_code == "NOT_IMPLEMENTED"


def test_terminate_reports_terminated_and_says_which_node_won(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))
    seen: list[object] = []

    async def sink(event: object) -> None:
        seen.append(event)

    result = asyncio.run(ProgramArtifactProvider().terminate("task-001", sink))

    assert result.status == "terminated"
    assert result.final_node_id == state_module.node_id_for("task-001", 0)
    assert [e.status for e in seen if isinstance(e, EventStatus)] == ["terminated"]


# -- the thread-to-loop bridge --------------------------------------------------


class _CannedEngine:
    """A search that emits a real event sequence and executes nothing.

    Standing in for `PuctEngine` so the bridge can be tested on its own: the
    search runs on a worker thread and its events have to arrive on the caller's
    event loop, which is the part that deadlocks if it is got wrong.
    """

    def __init__(self, script: list[dict[str, object]], **_: object) -> None:
        self._script = script
        self.saw_stop = False

    def run(self, spec: object, emit: object, should_stop: object) -> None:
        for event in self._script:
            emit({"createdAt": "", "event": event, "sequence": 0})  # type: ignore[operator]
        self.saw_stop = bool(should_stop())  # type: ignore[operator]


def _no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a search past the pre-flight without a scorecard that can rank.

    The probe is exercised on its own below; these tests are about the bridge
    between the search thread and the caller's loop, and a real pre-flight would
    refuse the fixture scorecard before any of that is reached.
    """
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.provider.run_probe",
        lambda spec: {"baseline": 0.25, "worsened": 0.05, "flat": False, "label": "test"},
    )


def _canned(monkeypatch: pytest.MonkeyPatch, script: list[dict[str, object]]) -> None:
    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_engine.PuctEngine",
        lambda **kwargs: _CannedEngine(script, **kwargs),
    )


_SCRIPT: list[dict[str, object]] = [
    events.seeded(0, 0.25, code_hash="sha256:aa"),
    events.expanded(1, 0, 1, 0.4, True, iteration=1, code_hash="sha256:bb"),
    events.evaluated(1, 0.42, {"rmse": 1.9}),
    events.merged(1, True, "it scored better"),
    events.cost(9_000, 0),
    events.search_finished("completed", 1, 1),
]


def test_a_search_running_on_a_thread_delivers_its_events_to_the_callers_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _canned(monkeypatch, _SCRIPT)
    provider = ProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))
    seen: list[object] = []

    async def sink(event: object) -> None:
        seen.append(event)

    result = asyncio.run(provider.run(request, sink))

    assert result.status == "completed"
    assert result.final_node_id == state_module.node_id_for("task-001", 1)
    kinds = [type(event).__name__ for event in seen]
    assert kinds == [
        "EventStatus",   # running, before anything is measured
        "EventNode",     # the root
        "EventNode",     # the candidate, once the merger ruled on it
        "EventProgress",
        "EventProgress",  # the cost sweep
        "EventStatus",   # completed, last
    ]
    assert [e.status for e in seen if isinstance(e, EventStatus)] == ["running", "completed"]


def test_the_search_waits_for_a_slow_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract makes the provider carry the queue's back-pressure: the
    events go to a bounded queue, and a search that fired and forgot would drop
    the node it just told everyone about."""
    _canned(monkeypatch, _SCRIPT)
    provider = ProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))
    order: list[str] = []

    async def sink(event: object) -> None:
        order.append(f"enter:{type(event).__name__}")
        await asyncio.sleep(0.01)
        order.append(f"leave:{type(event).__name__}")

    asyncio.run(provider.run(request, sink))

    # Never two enters in a row: the search thread blocked on each delivery.
    assert all(
        order[index].startswith("enter:") is not order[index + 1].startswith("enter:")
        for index in range(len(order) - 1)
    )


def test_terminate_reaches_a_search_that_is_already_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`should_stop` is polled between expansions, which is what makes a
    terminate land at a node boundary rather than mid sandbox run."""
    released = threading.Event()
    engines: list[_HaltingEngine] = []

    class _HaltingEngine:
        """Emits one node, then waits where a real search waits: between nodes."""

        def __init__(self, **_: object) -> None:
            self.stopped: bool | None = None
            engines.append(self)

        def run(self, spec: object, emit: object, should_stop: object) -> None:
            emit({"createdAt": "", "event": events.seeded(0, 0.25, code_hash="sha256:aa"),
                  "sequence": 0})  # type: ignore[operator]
            assert released.wait(5), "the test never released the search"
            self.stopped = bool(should_stop())  # type: ignore[operator]
            if self.stopped:
                emit({"createdAt": "", "event": events.search_finished("stopped", 0, 1),
                      "sequence": 0})  # type: ignore[operator]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_engine.PuctEngine",
        _HaltingEngine,
    )
    provider = ProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    async def drive() -> object:
        seeded = asyncio.Event()

        async def sink(event: object) -> None:
            if isinstance(event, EventNode):
                seeded.set()

        task = asyncio.ensure_future(provider.run(request, sink))
        # The search is now parked between nodes, exactly where a terminate has
        # to reach it -- so this is not a race the test happened to win.
        await seeded.wait()
        terminated = await provider.terminate(request.task_id)
        released.set()
        return terminated, await task

    terminated, result = asyncio.run(drive())

    assert engines[0].stopped is True
    assert terminated.status == "terminated"
    # And the run's own result agrees: not completed, because it did not finish.
    assert result.status == "terminated"


def test_a_crashing_search_becomes_a_failed_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Exploding:
        def __init__(self, **_: object) -> None:
            pass

        def run(self, spec: object, emit: object, should_stop: object) -> None:
            raise RuntimeError("the evaluator went away")

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_engine.PuctEngine",
        lambda **kwargs: _Exploding(**kwargs),
    )
    provider = ProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    result = asyncio.run(provider.run(request))

    assert result.status == "failed"
    assert result.error_code == "ENGINE_ERROR"
    assert "the evaluator went away" in (result.error_message or "")
    assert provider.read_state(request.task_id).status == "failed"


def test_a_scorecard_that_cannot_rank_is_refused_before_the_budget_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A search on flat terrain is a random walk that looks completely normal
    from outside: every event fires, every candidate is recorded, and the run
    reports that it found nothing. The pre-flight is what turns that into a
    sentence naming the scorecard -- so it has to run before the search, not
    after it has spent the model calls."""
    built: list[object] = []
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_engine.PuctEngine",
        lambda **kwargs: built.append(kwargs) or _CannedEngine(_SCRIPT, **kwargs),
    )
    provider = ProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path)
    # The fixture scorecard has no criteria, so nothing can be ranked with it.
    _scorecard(Path(request.run_dir))

    result = asyncio.run(provider.run(request))

    assert result.status == "failed"
    assert result.error_code == "PROBE_REFUSED"
    assert "criteria" in (result.error_message or "")
    # And no search was ever constructed, so no model call was paid for.
    assert built == []


# -- what a candidate can see ---------------------------------------------------


def test_a_candidate_is_not_handed_the_hosts_environment(tmp_path: Path) -> None:
    """The environment the sandbox builds is an allowlist, not the host's.

    This mattered the moment the search moved in-process. As a sidecar the
    surrounding environment held no provider key; in a host that runs the model
    it holds every one of them -- and `runtime.load_model_endpoint` actively
    tells operators to put the key there. Bubblewrap has always been clean
    (`--clearenv`); seatbelt inherited whatever the subprocess was given, and
    the host environment was being merged in.
    """
    import os
    import subprocess

    from openjiuwen.rsi.artifact_rsi.program_opt.sandbox import (
        detect_local_capability,
        sandbox_command,
    )

    capability = detect_local_capability()
    if not capability.available:
        pytest.skip("no isolation backend on this host")

    os.environ["PROGRAM_OPT_TEST_SECRET"] = "sk-must-not-be-visible"
    try:
        peek = tmp_path / "peek.py"
        peek.write_text(
            "import json, os\n"
            "print(json.dumps({'secret': os.environ.get('PROGRAM_OPT_TEST_SECRET'),\n"
            "                  'threads': os.environ.get('OMP_NUM_THREADS'),\n"
            "                  'home': os.environ.get('HOME')}))\n",
            encoding="utf-8",
        )
        command, env = sandbox_command(tmp_path, [str(peek)], capability=capability, timeout=30)
        completed = subprocess.run(
            command, capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60
        )
    finally:
        os.environ.pop("PROGRAM_OPT_TEST_SECRET", None)

    assert completed.returncode == 0, completed.stderr
    seen = json.loads(completed.stdout.strip().splitlines()[-1])
    assert seen["secret"] is None
    # Still gets what the sandbox does mean to give it: the thread cap that
    # keeps a candidate from burning its CPU budget eight ways at once, and a
    # writable home inside the scratch directory.
    assert seen["threads"] == "1"
    assert seen["home"] == str(tmp_path.resolve())


def test_importing_the_provider_does_not_require_the_search_engines_wheel() -> None:
    """`artifact_rsi/__init__` imports this provider eagerly, and it is
    re-exported from `openjiuwen.rsi`. So a module-level `agentdescent` import
    anywhere on the chain would make the whole RSI package unimportable wherever
    that wheel is absent -- and agent-core does not declare it. That is why
    `PuctEngine` is imported inside `_drive` rather than at the top.

    Checked in a fresh interpreter: within this session `agentdescent` is
    already in `sys.modules` from the tests above.
    """
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-c",
         "import sys; import openjiuwen.rsi;"
         " from openjiuwen.rsi import ProgramArtifactProvider;"
         " print('agentdescent' in sys.modules)"],
        capture_output=True, text=True, timeout=180,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip().splitlines()[-1] == "False"
