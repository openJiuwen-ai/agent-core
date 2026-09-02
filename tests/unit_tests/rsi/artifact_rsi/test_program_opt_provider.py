# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the ScienceDiscovery-backed program optimization provider.

The interesting surface is not the protocol -- `isinstance` settles that -- but
the three translations the provider performs: an injected model service into
the engine's completion seam, an isolation probe into a refusal, and nine
search events into the contract's three. Each is tested against the thing it
actually talks to (a live-shaped model fake, the vendored event constructors) rather than a
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
from openjiuwen.rsi.artifact_rsi.program_opt.puct_provider import (
    PuctProgramArtifactProvider,
)
from openjiuwen.rsi.artifact_rsi.program_opt.runtime import (
    DEFAULT_MAX_TOKENS_PER_CALL,
    ModelConfigError,
    SandboxUnavailable,
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


class _FakeModel:
    """An initialized model service, as AgentServer would inject one.

    Only what the provider is permitted to touch: an awaitable ``invoke``
    returning a message with ``content`` and ``usage_metadata``. Anything
    beyond that — IDs, config files, clients — is exactly what the contract
    forbids the provider from reaching for, so the fake does not have it.
    """

    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.calls = 0

    async def invoke(self, messages: object, **_kwargs: object) -> object:
        self.calls += 1

        class _Usage:
            input_tokens = 10
            output_tokens = 5

        class _Message:
            content = self.reply
            usage_metadata = _Usage()

        return _Message()


def _request(tmp_path: Path, **overrides: object) -> ArtifactEngineRequest:
    seed = tmp_path / "seed.py"
    seed.write_text(SEED, encoding="utf-8")
    values: dict[str, object] = {
        "task_id": "task-001",
        "run_dir": str(tmp_path / "run"),
        "artifact_path": str(seed),
        "model": _FakeModel(),
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
    provider = PuctProgramArtifactProvider()

    assert isinstance(provider, ArtifactProvider)
    assert provider.artifact_type == "program"
    assert get_type_hints(PuctProgramArtifactProvider)["artifact_type"] == Literal["program"]


# -- the model reference --------------------------------------------------------


def test_a_request_without_a_model_instance_fails_the_task(tmp_path: Path) -> None:
    """The contract routes the model through AgentServer as a live instance.

    Nothing here may fall back to reading a config file — that is exactly the
    client-building the contract forbids — so a missing instance is a failed
    task naming the resolution path, not a default.
    """
    provider = PuctProgramArtifactProvider(sandbox_backend="seatbelt")
    request = _request(tmp_path, model=None)
    _scorecard(Path(request.run_dir))

    result = asyncio.run(provider.run(request))

    assert result.status == "failed"
    assert result.error_code == "MODELCONFIG"
    assert "resource_mgr" in (result.error_message or "")


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
    """A seed that is not a runnable program — here, one reaching outside the
    sandbox — is refused while the user is still looking at the form, instead
    of failing every expansion identically after three paid model calls."""
    path = tmp_path / "seed.py"
    path.write_text("import requests\ndef fetch():\n    return requests.get('x')\n",
                    encoding="utf-8")

    result = PuctProgramArtifactProvider().validate_input(str(path))

    assert result.valid is False
    assert [error["code"] for error in result.errors] == ["ARTIFACT_REJECTED_BY_GATE"]


def test_a_seed_is_not_required_to_define_any_particular_function(tmp_path: Path) -> None:
    """`validate_input` sees a path, never the scorecard — so it cannot know
    what the evaluator will call. The gate used to demand `train_and_predict`,
    one ported task's contract, and thereby rejected every legitimate
    `custom_script` seed: a codec defining `compress`/`decompress` came back
    ARTIFACT_REJECTED_BY_GATE "missing train_and_predict function". Whether a
    seed is callable is decided where it can be — the evaluator's own import,
    and the probe that scores the seed before any budget is spent."""
    path = tmp_path / "codec.py"
    path.write_text(
        "def compress(text):\n    return text.encode()\n"
        "def decompress(data):\n    return data.decode()\n",
        encoding="utf-8",
    )

    assert PuctProgramArtifactProvider().validate_input(str(path)).valid is True


def test_an_empty_seed_reports_the_fact_once(tmp_path: Path) -> None:
    """ARTIFACT_EMPTY used to be followed by the gate's "empty source" — the
    same fact reported twice. Empty has nothing further to say."""
    path = tmp_path / "seed.py"
    path.write_text("", encoding="utf-8")

    result = PuctProgramArtifactProvider().validate_input(str(path))

    assert result.valid is False
    assert [error["code"] for error in result.errors] == ["ARTIFACT_EMPTY"]


def test_a_usable_seed_validates(tmp_path: Path) -> None:
    path = tmp_path / "seed.py"
    path.write_text(SEED, encoding="utf-8")

    assert PuctProgramArtifactProvider().validate_input(str(path)).valid is True


@pytest.mark.parametrize(
    ("artifact_path", "code"),
    [(None, "ARTIFACT_PATH_REQUIRED"), ("", "ARTIFACT_PATH_REQUIRED")],
)
def test_no_program_at_all_is_named_as_such(artifact_path: str | None, code: str) -> None:
    result = PuctProgramArtifactProvider().validate_input(artifact_path)

    assert [error["code"] for error in result.errors] == [code]


def test_a_path_that_is_not_there_is_named_as_such(tmp_path: Path) -> None:
    result = PuctProgramArtifactProvider().validate_input(str(tmp_path / "absent.py"))

    assert [error["code"] for error in result.errors] == ["ARTIFACT_NOT_FOUND"]


# -- the run spec ---------------------------------------------------------------


def test_a_run_without_a_scorecard_is_refused_rather_than_scored_by_a_guess(
    tmp_path: Path,
) -> None:
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    Path(request.run_dir).mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="scorecard"):
        provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)


def test_the_token_ceiling_comes_from_the_scorecard_when_it_says(
    tmp_path: Path,
) -> None:
    """The model instance is opaque — the contract hands over a service, not
    its settings — so the per-run ceiling is the task's to raise. A reasoning
    model at the 16k default spends the whole allowance on hidden thinking and
    returns an empty reply, and the scorecard is where a task says so."""
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), max_tokens_per_call=64_000)

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)

    assert spec.max_tokens_per_call == 64_000

    _scorecard(Path(request.run_dir))
    silent = provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)
    assert silent.max_tokens_per_call == 16_000   # RunSpec's own default


def test_the_starting_program_reaches_the_spec(tmp_path: Path) -> None:
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)

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
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), packages=[package])

    with pytest.raises(ValueError, match="bare distribution names"):
        provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)


def test_an_ordinary_pinned_dependency_still_gets_through(tmp_path: Path) -> None:
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), packages=["xgboost", "scikit-learn==1.5.0"])

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)

    assert spec.packages == ("xgboost", "scikit-learn==1.5.0")


def test_resuming_continues_the_numbering_the_first_attempt_stopped_at(
    tmp_path: Path,
) -> None:
    """Without the previous tree a resumed run would append node 0 on top of a
    graph that already has one, leaving a single index holding two candidates."""
    provider = PuctProgramArtifactProvider()
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

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=True)

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

    provider = PuctProgramArtifactProvider()
    assert provider.read_state("task-001").status == "created"
    assert provider.get_tree("task-001").nodes != []


def test_an_unknown_task_says_so_rather_than_pretending(tmp_path: Path) -> None:
    provider = PuctProgramArtifactProvider()

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

    provider = PuctProgramArtifactProvider()
    final = provider.locate_artifact("task-001")

    assert final.node_id == state_module.node_id_for("task-001", 1)
    assert final.sha256 == "b" * 64


def test_an_artifact_from_another_task_is_not_served(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:" + "a" * 64))

    with pytest.raises(FileNotFoundError):
        PuctProgramArtifactProvider().locate_artifact("task-001", "A-program:other:dead")


# -- execution control ----------------------------------------------------------


def test_a_missing_model_instance_fails_the_task_rather_than_the_server(
    tmp_path: Path,
) -> None:
    """A missing model is a task-level failure with a status the caller can
    see, not an exception AgentServer has to interpret."""
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path, model=None)
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
    provider = PuctProgramArtifactProvider(sandbox_backend="none")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    result = asyncio.run(provider.run(request))

    assert result.status == "failed"
    assert result.error_code == "SANDBOXUNAVAILABLE"


def test_pause_stops_at_a_node_boundary_and_resume_continues_the_same_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pause` is `terminate`'s stop mechanism with the one non-terminal
    outcome: the stopped search folds to `paused`, and `resume` picks the same
    tree back up where it stopped."""
    released = threading.Event()

    class _HaltingEngine:
        def __init__(self, **_: object) -> None:
            pass

        def run(self, spec: object, emit: object, should_stop: object) -> None:
            emit({"createdAt": "", "event": events.seeded(0, 0.25, code_hash="sha256:aa"),
                  "sequence": 0})  # type: ignore[operator]
            assert released.wait(5), "the test never released the search"
            assert should_stop()  # type: ignore[operator]
            emit({"createdAt": "", "event": events.search_finished("stopped", 0, 1),
                  "sequence": 0})  # type: ignore[operator]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _HaltingEngine,
    )
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    async def drive() -> tuple:
        seeded = asyncio.Event()

        async def sink(event: object) -> None:
            if isinstance(event, EventNode):
                seeded.set()

        task = asyncio.ensure_future(provider.run(request, sink))
        await seeded.wait()
        paused = await provider.pause(request.task_id)
        released.set()
        return paused, await task

    paused, result = asyncio.run(drive())

    assert paused.status == "paused"
    assert result.status == "paused"
    # Persisted as paused, which is exactly what makes it resumable.
    assert provider.read_state(request.task_id).status == "paused"

    # The real engine writes `tree.json` after every expansion; the halting
    # fake above does not, so the snapshot a resume reads back is seeded here.
    from openjiuwen.rsi.artifact_rsi.program_opt.candidates import TREE_FILE, write_tree_snapshot

    write_tree_snapshot(Path(request.run_dir) / TREE_FILE, {
        "tree": [{"index": 0, "parent": None, "code": SEED, "score": 0.25}],
        "baseline": {}, "tokens": 0,
    })

    resumed_specs: list = []

    class _Finishing:
        def __init__(self, **_: object) -> None:
            pass

        def run(self, spec: object, emit: object, should_stop: object) -> None:
            resumed_specs.append(spec)
            emit({"createdAt": "", "event": events.search_finished("succeeded", 0, 1),
                  "sequence": 0})  # type: ignore[operator]

    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _Finishing,
    )
    result = asyncio.run(provider.resume(request))

    assert result.status == "completed"
    # The same tree, not a fresh root: the paused attempt's nodes came along.
    assert resumed_specs and len(resumed_specs[0].resume_nodes) == 1


def test_a_task_already_in_flight_refuses_a_second_run_or_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two engines on one run_dir would both write state.json and tree.json,
    and the second registration would steal the stop flag — pause and
    terminate would only reach the newcomer. Refused before anything touches
    disk, with the running task untouched."""
    released = threading.Event()

    class _ParkedEngine:
        def __init__(self, **_: object) -> None:
            pass

        def run(self, spec: object, emit: object, should_stop: object) -> None:
            emit({"createdAt": "", "event": events.seeded(0, 0.25, code_hash="sha256:aa"),
                  "sequence": 0})  # type: ignore[operator]
            assert released.wait(5)
            emit({"createdAt": "", "event": events.search_finished("succeeded", 0, 1),
                  "sequence": 0})  # type: ignore[operator]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _ParkedEngine,
    )
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    async def drive() -> tuple:
        seeded = asyncio.Event()

        async def sink(event: object) -> None:
            if isinstance(event, EventNode):
                seeded.set()

        task = asyncio.ensure_future(provider.run(request, sink))
        await seeded.wait()
        second = await provider.run(request)
        third = await provider.resume(request)
        released.set()
        return second, third, await task

    second, third, result = asyncio.run(drive())

    assert second.error_code == "TASK_ALREADY_RUNNING"
    assert third.error_code == "TASK_ALREADY_RUNNING"
    # And the first search was untouched by either attempt.
    assert result.status == "completed"


def test_a_terminated_task_refuses_to_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Terminate is final — that is its whole difference from pause, and it is
    only real if resume enforces it."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))
    _absorb(run, events.search_finished("stopped", 0, 1))
    assert run.status == "terminated"

    constructed: list = []
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        lambda **kwargs: constructed.append(kwargs),
    )
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
    result = asyncio.run(provider.resume(_request(tmp_path)))

    assert result.status == "terminated"
    assert result.error_code == "TERMINATED_NOT_RESUMABLE"
    assert constructed == []          # the engine was never even built


def test_pausing_a_task_that_is_not_running_says_so(tmp_path: Path) -> None:
    result = asyncio.run(PuctProgramArtifactProvider().pause("task-001"))

    assert result.error_code == "TASK_NOT_RUNNING"
    assert result.status == "created"


def test_terminate_reports_terminated_and_says_which_node_won(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))
    seen: list[object] = []

    async def sink(event: object) -> None:
        seen.append(event)

    result = asyncio.run(PuctProgramArtifactProvider().terminate("task-001", sink))

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
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.run_probe",
        lambda spec: {"baseline": 0.25, "worsened": 0.05, "flat": False, "label": "test"},
    )


def _canned(monkeypatch: pytest.MonkeyPatch, script: list[dict[str, object]]) -> None:
    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
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
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
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
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
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
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _HaltingEngine,
    )
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
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
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        lambda **kwargs: _Exploding(**kwargs),
    )
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
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
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        lambda **kwargs: built.append(kwargs) or _CannedEngine(_SCRIPT, **kwargs),
    )
    provider = PuctProgramArtifactProvider(sandbox_backend="bwrap")
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
    it holds every one of them. Bubblewrap has always been clean
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


def test_the_search_engine_is_a_declared_dependency() -> None:
    """`agentdescent` moved from the `program-opt` extra into the main
    dependencies: this project ships as an application, so the exact pin
    burdens no third-party resolver, and the provider imports the engine at
    module scope with no fallback path. The two tests this replaces guarded
    the optional-extra story — import stays light without the wheel, a missing
    wheel names the extra — and both premises died with the promotion.
    """
    import tomllib

    root = Path(__file__).resolve()
    for parent in root.parents:
        if (parent / "pyproject.toml").is_file():
            pyproject = tomllib.loads((parent / "pyproject.toml").read_text(encoding="utf-8"))
            break
    else:  # pragma: no cover - the repo always has one
        pytest.fail("no pyproject.toml above this test")

    dependencies = pyproject["project"]["dependencies"]
    pins = [entry for entry in dependencies if entry.startswith("agentdescent==")]
    assert pins, "agentdescent must be a pinned main dependency"


# -- programs made of more than one file ----------------------------------------
#
# The genome is a file tree, serialised with `agentdescent.filetree` — upstream's
# own answer to "a directory as evolvable state", lossless because the engine
# caches evaluations on the rendered string. One file is the common shape, not
# the only one.


def _tree(tmp_path: Path) -> Path:
    """A seed that is a package: an entrypoint plus a helper it imports."""
    root = tmp_path / "seed"
    (root / "helpers").mkdir(parents=True)
    (root / "candidate.py").write_text(
        "from helpers.scale import factor\n"
        "def train_and_predict(train, test):\n"
        "    return [x * factor() for x in test]\n",
        encoding="utf-8",
    )
    (root / "helpers" / "__init__.py").write_text("", encoding="utf-8")
    (root / "helpers" / "scale.py").write_text(
        "def factor():\n    return 3.0\n", encoding="utf-8"
    )
    return root


def test_a_directory_is_a_program(tmp_path: Path) -> None:
    provider = PuctProgramArtifactProvider()

    assert provider.validate_input(str(_tree(tmp_path))).valid is True


def test_a_directory_seed_reaches_the_spec_whole(tmp_path: Path) -> None:
    from openjiuwen.rsi.artifact_rsi.program_opt.program import files_of

    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path, artifact_path=str(_tree(tmp_path)))
    _scorecard(Path(request.run_dir))

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)

    assert sorted(files_of(spec.baseline_code)) == [
        "candidate.py", "helpers/__init__.py", "helpers/scale.py",
    ]
    assert spec.entrypoint == "candidate.py"


def test_a_tree_with_no_obvious_entrypoint_is_asked_about_rather_than_guessed(
    tmp_path: Path,
) -> None:
    """Guessing would send every candidate to an evaluator importing the wrong
    module, and the run would report that the program never works."""
    root = tmp_path / "seed"
    root.mkdir()
    (root / "model.py").write_text("def a(): pass\n", encoding="utf-8")
    (root / "features.py").write_text("def b(): pass\n", encoding="utf-8")

    result = PuctProgramArtifactProvider().validate_input(str(root))

    assert result.valid is False
    assert [error["code"] for error in result.errors] == ["ARTIFACT_ENTRYPOINT_UNCLEAR"]
    assert "entrypoint" in result.errors[0]["message"]


def test_one_python_file_in_a_directory_needs_no_asking(tmp_path: Path) -> None:
    root = tmp_path / "seed"
    root.mkdir()
    (root / "solver.py").write_text(SEED, encoding="utf-8")
    (root / "notes.md").write_text("# how it works\n", encoding="utf-8")

    assert PuctProgramArtifactProvider().validate_input(str(root)).valid is True


def test_a_scorecard_naming_a_file_the_program_does_not_have_is_refused(
    tmp_path: Path,
) -> None:
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path, artifact_path=str(_tree(tmp_path)))
    _scorecard(Path(request.run_dir), entrypoint="main.py")

    with pytest.raises(ValueError, match="main.py"):
        provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)


def test_a_reply_carries_only_what_it_changed(tmp_path: Path) -> None:
    """A model asked to restate ten files to change one spends the tokens on
    nine copies and rewrites the nine. A path that does not appear is
    inherited, so what the search records is the edit."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import extract_files

    parent = {
        "candidate.py": "from helpers.scale import factor\ndef train_and_predict(t, s): ...\n",
        "helpers/scale.py": "def factor():\n    return 3.0\n",
    }
    reply = (
        "Only the scale needed changing.\n\n"
        "```python name=helpers/scale.py\n"
        '"""Fit the factor instead of fixing it."""\n'
        "def factor():\n    return 7.0\n"
        "```\n"
    )

    files, summary = extract_files(reply, parent)

    assert files["candidate.py"] == parent["candidate.py"]
    assert "7.0" in files["helpers/scale.py"]
    # The label comes off the file that changed, not off the entrypoint that
    # did not — otherwise every helper edit would be labelled with a docstring
    # nobody touched, or with nothing at all.
    assert summary == "Fit the factor instead of fixing it."


@pytest.mark.parametrize(
    "reply",
    [
        "```python name=helpers/scale.py\ndef factor(): return 7.0\n```",
        "```python:helpers/scale.py\ndef factor(): return 7.0\n```",
        "### helpers/scale.py\n```python\ndef factor(): return 7.0\n```",
        "**helpers/scale.py**\n```\ndef factor(): return 7.0\n```",
    ],
)
def test_the_four_ways_a_model_labels_a_file_all_land(reply: str) -> None:
    """The format is described to the model, not enforced on it. These are the
    spellings that come back."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import extract_files

    parent = {"candidate.py": "x = 1\n", "helpers/scale.py": "def factor(): return 3.0\n"}

    files, _ = extract_files(reply, parent)

    assert "7.0" in files["helpers/scale.py"]
    assert files["candidate.py"] == parent["candidate.py"]


def test_an_unlabelled_block_is_still_the_entrypoint() -> None:
    """What a one-file run looks like, and what upstream's parser assumed."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import extract_files

    parent = {"candidate.py": "x = 1\n", "helpers/scale.py": "y = 2\n"}

    files, _ = extract_files("```python\nx = 99\n```", parent)

    assert files["candidate.py"] == "x = 99"
    assert files["helpers/scale.py"] == "y = 2\n"


def test_a_file_can_be_removed_but_never_the_entrypoint() -> None:
    """A program that can only grow accumulates dead modules the search then
    pays to carry in every prompt. Removing the entrypoint would leave the
    evaluator nothing to import, so every later candidate would fail alike."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import extract_files

    parent = {"candidate.py": "x = 1\n", "helpers/old.py": "y = 2\n"}

    assert sorted(extract_files("DELETE helpers/old.py\n", parent)[0]) == ["candidate.py"]
    assert sorted(extract_files("DELETE candidate.py\n", parent)[0]) == [
        "candidate.py", "helpers/old.py",
    ]


def test_the_serialised_tree_survives_a_round_trip() -> None:
    """The genome is also the evaluation cache key, so two different programs
    must never render to the same string."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import bundle, files_of

    tree = {"candidate.py": "x = 1\n", "a/b.py": '"""tricky ``` content"""\n'}

    assert files_of(bundle(tree)) == tree
    assert bundle(tree) != bundle({**tree, "a/b.py": "other"})


def test_plain_source_is_still_read_as_a_one_file_program() -> None:
    """What a run written before any of this contains."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import files_of

    assert files_of("def train_and_predict(): ...") == {
        "candidate.py": "def train_and_predict(): ..."
    }


def test_every_file_is_listed_in_the_prompt() -> None:
    """A model cannot edit a helper it was never shown."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import bundle
    from openjiuwen.rsi.artifact_rsi.program_opt.prompt import render_tree

    rendered = render_tree(bundle({
        "candidate.py": "import helpers.scale\n",
        "helpers/scale.py": "def factor(): return 3.0\n",
    }))

    assert "name=candidate.py" in rendered
    assert "name=helpers/scale.py" in rendered
    # Entrypoint first: the file the evaluator imports is the one that makes
    # the others make sense.
    assert rendered.index("name=candidate.py") < rendered.index("name=helpers/scale.py")


def test_a_one_file_program_is_still_shown_the_way_it_always_was() -> None:
    from openjiuwen.rsi.artifact_rsi.program_opt.prompt import render_tree

    assert render_tree("def f(): pass") == "```python\ndef f(): pass\n```"


def test_a_package_candidate_runs_in_the_sandbox(tmp_path: Path) -> None:
    """The whole point, end to end: a candidate that is three files, one of them
    in a subpackage, imported and scored by an evaluator that did not have to
    know any of that. The evaluator contract is unchanged — it is still told the
    entrypoint's path through `SCIENCE_AGENT_CANDIDATE`."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import bundle
    from openjiuwen.rsi.artifact_rsi.program_opt.sandbox import detect_local_capability
    from openjiuwen.rsi.artifact_rsi.program_opt.script_domain import _run_evaluator

    capability = detect_local_capability()
    if not capability.available:
        pytest.skip("no isolation backend on this host")

    tree = {
        "candidate.py": "from helpers.scale import factor\n"
                        "def train_and_predict(train, test):\n"
                        "    return [x * factor() for x in test]\n",
        "helpers/__init__.py": "",
        "helpers/scale.py": "def factor():\n    return 3.0\n",
    }
    evaluator = (
        "import importlib, json, os\n"
        'entry = os.environ["SCIENCE_AGENT_CANDIDATE"]\n'
        'mod = importlib.import_module(entry.removesuffix(".py").replace("/", "."))\n'
        "got = mod.train_and_predict([], [1.0, 2.0])\n"
        'json.dump({"valid": True, "metrics": {"total": sum(got)}},\n'
        '          open(os.environ["SCIENCE_AGENT_RESULT"], "w"))\n'
    )

    payload = _run_evaluator(bundle(tree), evaluator, [0],
                             capability=capability, timeout=60)

    assert payload["valid"] is True
    assert payload["metrics"]["total"] == pytest.approx(9.0)


def test_a_candidate_is_stored_as_a_directory_you_can_open(tmp_path: Path) -> None:
    """The run directory is meant to be readable: the files are the program, at
    their own paths, not a blob someone has to decode first. The serialised tree
    sits beside them so a resumed run rebuilds the exact string the hash was
    taken over."""
    from openjiuwen.rsi.artifact_rsi.program_opt.candidates import CandidateStore
    from openjiuwen.rsi.artifact_rsi.program_opt.program import bundle

    store = CandidateStore(tmp_path, flat=True)
    code = bundle({"candidate.py": "x = 1\n", "helpers/scale.py": "y = 2\n"})

    digest = store.put("run-1", code)
    directory = store.path_for("run-1", digest)

    assert (directory / "candidate.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (directory / "helpers" / "scale.py").read_text(encoding="utf-8") == "y = 2\n"
    assert store.get("run-1", digest) == code


def test_a_program_may_import_its_own_modules(tmp_path: Path) -> None:
    """The gate asks `find_spec` — "is this installed" — which is the wrong
    question for a sibling module. Without the program's own roots it refuses
    every multi-file program for importing itself."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import local_roots, validate_source

    source = "from helpers.scale import factor\ndef train_and_predict(t, s): return factor()\n"

    assert validate_source(source)[0] is False
    assert validate_source(source, local=local_roots(["helpers/scale.py"]))[0] is True
    # A blocked import stays blocked even when a file of that name is in the
    # tree: shadowing `os` must not be the way past the gate.
    assert validate_source(
        "import os\ndef train_and_predict(t, s): ...\n", local=local_roots(["os.py"]),
    )[0] is False


def test_a_reply_that_proposed_nothing_is_an_empty_draw_not_a_copy() -> None:
    """Once a reply became a patch, "the model returned nothing" stopped being
    visible: merging nothing onto the parent yields the parent, a valid program
    that costs a full sandboxed evaluation to learn the parent's own score, and
    puts a duplicate node on the tree. Upstream turns an empty draw into a node
    scoring `-inf` — deliberately a node, because dropping it shrinks the rank
    denominator and raises `1/N` for every later iteration."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import reply_carries_program

    assert reply_carries_program("") is False
    assert reply_carries_program("   \n\n ") is False
    assert reply_carries_program("```python name=a.py\nx = 1\n```") is True
    assert reply_carries_program("```python\nx = 1\n```") is True
    assert reply_carries_program("DELETE helpers/old.py") is True
    # Upstream's fenceless fallback: prose is taken as the program, becomes a
    # candidate that cannot parse, and scores `-inf`. That is a draw that
    # happened and failed, not a draw that never happened.
    assert reply_carries_program("I could not think of anything better.") is True


def test_the_final_artifact_is_found_when_two_nodes_hold_the_same_program(
    tmp_path: Path,
) -> None:
    """Candidates are addressed by the hash of their content, so two nodes that
    arrived at the same program share one artifact — and then `node_id` on that
    artifact can only name one of them. Resolving the final artifact by scanning
    for a matching `node_id` therefore misses whenever the winner is not the node
    the artifact ended up recorded under, and falls through to an arbitrary
    entry. Every node carries its own `snapshot_artifact_id`; that is the lookup.
    """
    run = _state(tmp_path)
    same = "sha256:" + "c" * 64
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:" + "a" * 64))
    # Node 1 wins. Node 2 is the same program, and is the one the shared
    # artifact ends up naming.
    _absorb(run, events.expanded(1, 0, 1, 0.9, True, iteration=1, code_hash=same))
    _absorb(run, events.evaluated(1, 0.90, {"rmse": 1.0}))
    _absorb(run, events.merged(1, True, "it scored better"))
    _absorb(run, events.expanded(2, 0, 1, 0.9, True, iteration=2, code_hash=same))
    _absorb(run, events.evaluated(2, 0.90, {"rmse": 1.0}))
    _absorb(run, events.merged(2, False, "the same program again"))
    # A later, different candidate — so falling through to "the last artifact
    # recorded" returns something visibly wrong rather than the right answer by
    # accident.
    _absorb(run, events.expanded(3, 0, 1, 0.1, True, iteration=3,
                                 code_hash="sha256:" + "d" * 64))
    _absorb(run, events.evaluated(3, 0.10, {"rmse": 9.0}))
    _absorb(run, events.merged(3, False, "it scored worse"))

    final = PuctProgramArtifactProvider().locate_artifact("task-001")

    assert final.sha256 == "c" * 64
    assert final.node_id == state_module.node_id_for("task-001", 1)


def test_the_final_artifact_is_found_when_the_winner_is_the_later_duplicate(
    tmp_path: Path,
) -> None:
    """The mirror of the case above, and the one that a `node_id` scan cannot
    survive whichever node the shared artifact names: here the winner is the
    *second* node to reach the program, and the artifact names the first."""
    run = _state(tmp_path)
    same = "sha256:" + "c" * 64
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:" + "a" * 64))
    _absorb(run, events.expanded(1, 0, 1, 0.4, True, iteration=1, code_hash=same))
    _absorb(run, events.evaluated(1, 0.40, {"rmse": 2.0}))
    _absorb(run, events.merged(1, False, "it scored worse on this split"))
    _absorb(run, events.expanded(2, 0, 1, 0.9, True, iteration=2, code_hash=same))
    _absorb(run, events.evaluated(2, 0.90, {"rmse": 1.0}))
    _absorb(run, events.merged(2, True, "it scored better"))
    _absorb(run, events.expanded(3, 0, 1, 0.1, True, iteration=3,
                                 code_hash="sha256:" + "d" * 64))
    _absorb(run, events.evaluated(3, 0.10, {"rmse": 9.0}))
    _absorb(run, events.merged(3, False, "it scored worse"))

    final = PuctProgramArtifactProvider().locate_artifact("task-001")

    assert final.sha256 == "c" * 64


def test_the_engine_has_no_model_channel_of_its_own() -> None:
    """Every model call goes through the request's injected ``Model``.

    The engine used to fall back to building an HTTP client from
    ``spec.llm_url`` when no factory was injected — the one path that could
    bypass the injection, and a bypass that exists is a bypass that eventually
    gets used. Construction now demands the factory outright.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import PuctEngine

    with pytest.raises(TypeError):
        PuctEngine()                                   # type: ignore[call-arg]
    with pytest.raises(ValueError, match="injected Model"):
        PuctEngine(completion_factory=None)            # type: ignore[arg-type]


# -- task-owned prompt wording ---------------------------------------------------


def test_a_tasks_mutation_template_reaches_the_real_prompt(tmp_path: Path) -> None:
    """Different tasks need differently assembled prompts, so the wording is
    task data (`run_dir/prompts/mutation.md`) rendered over the framework's
    slots. Driven through the real domain closure, not a copy of it — and the
    template deliberately contains JSON braces, which is why the syntax is
    `${...}` (`string.Template`) and not `str.format`."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import Program, program_id
    from openjiuwen.rsi.artifact_rsi.program_opt.sandbox import SandboxCapability
    from openjiuwen.rsi.artifact_rsi.program_opt.script_domain import script_domain

    domain = script_domain(
        scorecard={"criteria": [{
            "id": "score", "name": "score", "direction": "maximize",
            "weight": 1.0, "normalize": {"kind": "identity"},
            "measure": {"kind": "custom_script", "scriptCas": "sha256:x",
                        "split": {"gateShards": 4, "rolloutShards": 2,
                                  "testShards": 2, "shardRows": 1, "seed": 1,
                                  "trainRows": None}, "timeoutSeconds": 60},
        }]},
        script='"""contract doc"""\nprint(1)\n',
        capability=SandboxCapability(backend="seatbelt"),
        statement="pack the bins tighter",
        baseline_code="def pack():\n    return []\n",
        mutation_template=(
            "TASK SAYS: ${statement}\n"
            'a JSON example the model should copy: {"bins": [1, 2]}\n'
            "PROGRAM:\n${parent_code}\n"
        ),
    )
    code = "def pack():\n    return [[0]]\n"
    prompt = domain.prompt(Program(
        program_id=program_id(code), iteration=1, parent_id=None, code=code,
        change_summary="", metrics={"score": 0.4}, valid=True, error="",
    ))

    assert "TASK SAYS: pack the bins tighter" in prompt
    assert '{"bins": [1, 2]}' in prompt          # braces survive rendering
    assert "def pack()" in prompt                 # the slot was filled
    assert "fixed evaluator script" not in prompt  # the built-in template stepped aside


def test_an_unknown_placeholder_is_refused_by_name_at_load(tmp_path: Path) -> None:
    """`safe_substitute` would leave `${statment}` in the prompt as literal
    text, and the model would optimise against a prompt with a hole in it for
    the whole budget. The load is the one moment the mistake can still be a
    sentence."""
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_provider import _prompt_templates

    prompts = tmp_path / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "mutation.md").write_text("goal: ${statment}", encoding="utf-8")

    with pytest.raises(ValueError) as refusal:
        _prompt_templates(tmp_path)

    assert "${statment}" in str(refusal.value)          # the typo, by name
    assert "${statement}" in str(refusal.value)         # and the vocabulary to fix it


def test_prompt_templates_land_on_the_spec(tmp_path: Path) -> None:
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))
    prompts = Path(request.run_dir) / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "mutation.md").write_text("M ${statement}", encoding="utf-8")
    (prompts / "repair.md").write_text("R ${error}\n${code}", encoding="utf-8")
    (prompts / "prior.md").write_text("${prompt}\nRate it.", encoding="utf-8")

    spec = provider._spec_for(request, SandboxCapability(backend="bwrap"), resumed=False)

    assert spec.mutation_template == "M ${statement}"
    assert spec.repair_template.startswith("R ")
    assert spec.prior_template.endswith("Rate it.")


def test_repair_and_prior_render_their_task_templates() -> None:
    from openjiuwen.rsi.artifact_rsi.program_opt.prompt import (
        repair_prompt,
        with_promise_request,
    )

    fixed = repair_prompt("def f():\n    return 1\n", "IndexError: x",
                          template="FIX THIS: ${error}\n${code}\nusing ${imports}")
    assert fixed.startswith("FIX THIS: IndexError: x")
    assert "def f()" in fixed and "numpy" in fixed

    rated = with_promise_request("THE PROMPT", template="${prompt}\n\nGive a number.")
    assert rated == "THE PROMPT\n\nGive a number."


def test_the_engine_wires_the_task_templates_not_the_defaults() -> None:
    """The three call sites, pinned in the real source: a template that lands
    on the spec but is never passed onward is wording the task chose and the
    model never saw."""
    import inspect

    from openjiuwen.rsi.artifact_rsi.program_opt import puct_engine

    source = inspect.getsource(puct_engine)
    assert "mutation_template=spec.mutation_template" in source
    assert "template=spec.repair_template" in source
    assert "with_promise_request(prompt, spec.prior_template)" in source
