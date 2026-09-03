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
import time
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
)
from openjiuwen.rsi.artifact_rsi.program_opt.execution import (
    ExecutionOutcome,
    ExecutionUnavailable,
    execution_from_sys_operation,
)
from openjiuwen.rsi.artifact_rsi.program_opt.state import ProgramRunState
from openjiuwen.rsi.artifact_rsi.provider import ArtifactProvider
from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.events import EventNode, EventProgress, EventStatus

def _local_execution(files, command, env, timeout, result_file):
    """The seam, realised with a plain subprocess for tests.

    Honest here and nowhere else: a test executes only text the test itself
    wrote. Production has exactly one channel — the injected SysOperation —
    and this stand-in exists so the evaluation pipeline (staging, shim,
    result parsing, fallbacks) keeps being exercised for real.
    """
    import subprocess
    import sys
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory(prefix="evolve-test-exec-") as scratch:
        root = _Path(scratch)
        for path, text in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        argv = [sys.executable, *command[1:]] if command and command[0] == "python" else list(command)
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, cwd=scratch,
                env={"PATH": "/usr/bin:/bin", **env}, timeout=timeout + 30,
            )
        except subprocess.TimeoutExpired:
            return ExecutionOutcome(exit_code=None, output="timed out", result_text=None)
        result_text = None
        if result_file is not None and (root / result_file).exists():
            result_text = (root / result_file).read_text(encoding="utf-8")
        return ExecutionOutcome(
            exit_code=completed.returncode,
            output=(completed.stderr or "") + (completed.stdout or ""),
            result_text=result_text,
        )


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
    provider = PuctProgramArtifactProvider(execution=_local_execution)
    request = _request(tmp_path, model=None)
    _scorecard(Path(request.run_dir))

    result = asyncio.run(provider.run(request))

    assert result.status == "failed"
    assert result.error_code == "MODELCONFIG"
    assert "resource_mgr" in (result.error_message or "")


# -- isolation ------------------------------------------------------------------


def test_no_execution_channel_means_no_run(tmp_path: Path) -> None:
    """The contract has no sandbox field, so this refusal is the only thing
    standing between model-written code and the task's own model key: with
    neither a SysOperation nor an execution injected, the run fails before
    anything is staged."""
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    result = asyncio.run(provider.run(request))

    assert result.status == "failed"
    assert result.error_code == "EXECUTIONUNAVAILABLE"
    assert "SysOperationCard" in (result.error_message or "")


def test_the_sys_operation_bridge_refuses_nothing_injected() -> None:
    with pytest.raises(ExecutionUnavailable):
        execution_from_sys_operation(None, loop=None)


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
        provider._spec_for(request, resumed=False)


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

    spec = provider._spec_for(request, resumed=False)

    assert spec.max_tokens_per_call == 64_000

    _scorecard(Path(request.run_dir))
    silent = provider._spec_for(request, resumed=False)
    assert silent.max_tokens_per_call == 16_000   # RunSpec's own default


def test_the_starting_program_reaches_the_spec(tmp_path: Path) -> None:
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    spec = provider._spec_for(request, resumed=False)

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
        provider._spec_for(request, resumed=False)


def test_an_ordinary_pinned_dependency_still_gets_through(tmp_path: Path) -> None:
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), packages=["xgboost", "scikit-learn==1.5.0"])

    spec = provider._spec_for(request, resumed=False)

    assert spec.packages == ("xgboost", "scikit-learn==1.5.0")


def test_provisioning_probes_before_it_installs() -> None:
    """`ensure` is reached more than once per run — the discrimination probe
    and the engine pre-flight both call it — and pip resolves for seconds even
    when every requirement is already satisfied. A package that already
    imports must therefore cost one probe and no pip; a version pin rides the
    same fast path, satisfied by importability exactly as the old host-side
    `find_spec` treated it."""
    from openjiuwen.rsi.artifact_rsi.program_opt.provision import ensure

    commands: list[list[str]] = []

    def warm_execute(files, command, env, timeout, result_file):
        commands.append(list(command))
        return ExecutionOutcome(exit_code=0, output="", result_text=None)

    installed, note = ensure(["xgboost", "scikit-learn==1.5.0"], warm_execute)

    assert installed == []
    assert "already importable" in note
    assert len(commands) == 1 and commands[0][:2] == ["python", "-c"]
    assert not any("pip" in part for command in commands for part in command)


def test_a_package_the_probe_cannot_find_is_installed_and_verified() -> None:
    """The slow path still exists and still verifies: when the import probe
    fails, pip runs, and the same probe then checks what pip claims."""
    from openjiuwen.rsi.artifact_rsi.program_opt.provision import ensure

    commands: list[list[str]] = []
    probes = iter([1, 0])  # the pre-install probe fails, the verification passes

    def cold_execute(files, command, env, timeout, result_file):
        commands.append(list(command))
        if command[:4] == ["python", "-m", "pip", "install"]:
            return ExecutionOutcome(exit_code=0, output="", result_text=None)
        return ExecutionOutcome(
            exit_code=next(probes), output="No module named 'xgboost'", result_text=None,
        )

    installed, note = ensure(["xgboost"], cold_execute)

    assert installed == ["xgboost"]
    assert "installed" in note
    assert [command[:4] for command in commands] == [
        ["python", "-c", "import xgboost"][:4],
        ["python", "-m", "pip", "install"],
        ["python", "-c", "import xgboost"][:4],
    ]


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
        }),
        encoding="utf-8",
    )

    spec = provider._spec_for(request, resumed=True)

    assert spec.resume_from_sequence == 2
    assert len(spec.resume_nodes) == 2
    assert spec.resume_baseline == {"rmse": 2.5}


# -- the projection -------------------------------------------------------------


def _state(tmp_path: Path, total: int = 3) -> ProgramRunState:
    return ProgramRunState(task_id="task-001", run_dir=tmp_path / "run", total_iterations=total)


def _absorb(run: ProgramRunState, event: dict[str, object]) -> list[object]:
    return list(run.absorb(event))


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
    provider = PuctProgramArtifactProvider(execution=_local_execution)
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
            emit(events.seeded(0, 0.25, code_hash="sha256:aa"))  # type: ignore[operator]
            assert released.wait(5), "the test never released the search"
            assert should_stop()  # type: ignore[operator]
            emit(events.search_finished("stopped", 0, 1))  # type: ignore[operator]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _HaltingEngine,
    )
    provider = PuctProgramArtifactProvider(execution=_local_execution)
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
            emit(events.search_finished("succeeded", 0, 1))  # type: ignore[operator]

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
            emit(events.seeded(0, 0.25, code_hash="sha256:aa"))  # type: ignore[operator]
            assert released.wait(5)
            emit(events.search_finished("succeeded", 0, 1))  # type: ignore[operator]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _ParkedEngine,
    )
    provider = PuctProgramArtifactProvider(execution=_local_execution)
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
    provider = PuctProgramArtifactProvider(execution=_local_execution)
    result = asyncio.run(provider.resume(_request(tmp_path)))

    assert result.status == "terminated"
    assert result.error_code == "TERMINATED_NOT_RESUMABLE"
    assert constructed == []          # the engine was never even built


def test_pausing_a_task_that_is_not_running_says_so(tmp_path: Path) -> None:
    result = asyncio.run(PuctProgramArtifactProvider().pause("task-001"))

    assert result.error_code == "TASK_NOT_RUNNING"
    assert result.status == "created"


def test_terminate_reports_terminated_and_says_which_node_won(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The earlier version of this test terminated a task with no live search
    and passed on the answer being faked; it now drives a real one."""
    released = threading.Event()

    class _ParkedEngine:
        def __init__(self, **_: object) -> None:
            pass

        def run(self, spec: object, emit: object, should_stop: object) -> None:
            emit(events.seeded(0, 0.25, code_hash="sha256:aa"))  # type: ignore[operator]
            assert released.wait(5)
            emit(events.search_finished("stopped", 0, 1))  # type: ignore[operator]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _ParkedEngine,
    )
    provider = PuctProgramArtifactProvider(execution=_local_execution)
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))
    seen: list[object] = []

    async def drive() -> object:
        seeded = asyncio.Event()

        async def sink(event: object) -> None:
            seen.append(event)
            if isinstance(event, EventNode):
                seeded.set()

        task = asyncio.ensure_future(provider.run(request, sink))
        await seeded.wait()
        terminated = await provider.terminate(request.task_id, sink)
        released.set()
        await task
        return terminated

    result = asyncio.run(drive())

    assert result.status == "terminated"
    assert result.final_node_id == state_module.node_id_for("task-001", 0)
    assert "terminated" in [e.status for e in seen if isinstance(e, EventStatus)]


def test_terminating_a_task_that_is_not_running_says_so(tmp_path: Path) -> None:
    """Saying "terminated" about a completed task is a status change the next
    `read_state` contradicts, delivered to the event stream as fact. The one
    idempotent success is terminating a task that already is terminated."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))
    _absorb(run, events.search_finished("succeeded", 0, 1))
    run.finish()
    seen: list[object] = []

    async def sink(event: object) -> None:
        seen.append(event)

    result = asyncio.run(PuctProgramArtifactProvider().terminate("task-001", sink))

    assert result.status == "completed"                    # 真实状态，不是谎言
    assert result.error_code == "TASK_NOT_RUNNING"
    assert seen == []                                      # 没有假的状态变更事件


def test_terminating_twice_is_an_idempotent_success(tmp_path: Path) -> None:
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aa"))
    _absorb(run, events.search_finished("stopped", 0, 1))
    assert run.status == "terminated"

    result = asyncio.run(PuctProgramArtifactProvider().terminate("task-001"))

    assert result.status == "terminated"
    assert result.error_code is None                       # 已是终态，幂等成功


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
            emit(event)  # type: ignore[operator]
        self.saw_stop = bool(should_stop())  # type: ignore[operator]


def _no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a search past the pre-flight without a scorecard that can rank.

    The probe is exercised on its own below; these tests are about the bridge
    between the search thread and the caller's loop, and a real pre-flight would
    refuse the fixture scorecard before any of that is reached.
    """
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.run_probe",
        lambda spec, execute: {"baseline": 0.25, "worsened": 0.05, "flat": False, "label": "test"},
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
    {"type": "a_kind_this_version_does_not_know", "nodeIndex": 1},
    events.search_finished("completed", 1, 1),
]


def test_a_search_running_on_a_thread_delivers_its_events_to_the_callers_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _canned(monkeypatch, _SCRIPT)
    provider = PuctProgramArtifactProvider(execution=_local_execution)
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
        "EventProgress",  # per merged node; a cost event no longer emits one
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
    provider = PuctProgramArtifactProvider(execution=_local_execution)
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
            emit(events.seeded(0, 0.25, code_hash="sha256:aa"))  # type: ignore[operator]
            assert released.wait(5), "the test never released the search"
            self.stopped = bool(should_stop())  # type: ignore[operator]
            if self.stopped:
                emit(events.search_finished("stopped", 0, 1))  # type: ignore[operator]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _HaltingEngine,
    )
    provider = PuctProgramArtifactProvider(execution=_local_execution)
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
    provider = PuctProgramArtifactProvider(execution=_local_execution)
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
    provider = PuctProgramArtifactProvider(execution=_local_execution)
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


def test_a_candidate_is_not_handed_the_hosts_environment() -> None:
    """The environment an evaluation carries is an allowlist, not the host's.

    This mattered the moment the search moved in-process: in a host that runs
    the model, `os.environ` holds every provider key. The provider-local
    sandboxes are gone, so the property now lives at the seam — the evaluation
    pipeline hands the execution exactly the three declared names, and never
    reaches for the host environment. What the gateway sandbox adds around
    them is its business; a secret can only leak through what *this* side
    passes."""
    import os

    from openjiuwen.rsi.artifact_rsi.program_opt.program import bundle
    from openjiuwen.rsi.artifact_rsi.program_opt.script_domain import (
        CANDIDATE_ENV,
        RESULT_ENV,
        SHARDS_ENV,
        _run_evaluator,
    )

    handed: list[dict[str, str]] = []

    def peeking_execute(files, command, env, timeout, result_file):
        handed.append(dict(env))
        return _local_execution(files, command, env, timeout, result_file)

    evaluator = (
        "import json, os\n"
        'json.dump({"valid": True,\n'
        '           "metrics": {"secret_seen": int("PROGRAM_OPT_TEST_SECRET" in os.environ)}},\n'
        '          open(os.environ["SCIENCE_AGENT_RESULT"], "w"))\n'
    )

    os.environ["PROGRAM_OPT_TEST_SECRET"] = "sk-must-not-be-visible"
    try:
        payload = _run_evaluator(
            bundle({"candidate.py": "x = 1\n"}), evaluator, [0],
            execute=peeking_execute, timeout=60,
        )
    finally:
        os.environ.pop("PROGRAM_OPT_TEST_SECRET", None)

    # The seam received the three declared names and nothing else — no merge
    # of `os.environ`, however convenient one would have been.
    assert handed and set(handed[0]) == {CANDIDATE_ENV, RESULT_ENV, SHARDS_ENV}
    # And the candidate, run with exactly that allowlist, could not see the
    # secret sitting in this process's environment.
    assert payload["valid"] is True
    assert payload["metrics"]["secret_seen"] == 0


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

    spec = provider._spec_for(request, resumed=False)

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
        provider._spec_for(request, resumed=False)


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


def test_a_one_file_program_is_shown_with_its_path() -> None:
    """This test used to assert the opposite, and the opposite was wrong.

    A one-file listing was deliberately left unlabelled so the common case
    would not look more complicated than it is. What that cost is in
    `test_a_one_file_listing_still_shows_its_path`: the model had no path to
    copy and invented one, and a whole run's expansions scored the parent.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.prompt import render_tree

    assert render_tree("def f(): pass") == "```python name=candidate.py\ndef f(): pass\n```"


def test_a_package_candidate_runs_through_the_execution_seam(tmp_path: Path) -> None:
    """The whole point, end to end: a candidate that is three files, one of them
    in a subpackage, imported and scored by an evaluator that did not have to
    know any of that. The evaluator contract is unchanged — it is still told the
    entrypoint's path through `SCIENCE_AGENT_CANDIDATE`."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import bundle
    from openjiuwen.rsi.artifact_rsi.program_opt.script_domain import _run_evaluator

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
                             execute=_local_execution, timeout=60)

    assert payload["valid"] is True
    assert payload["metrics"]["total"] == pytest.approx(9.0)


def test_the_domain_scores_a_candidate_through_the_seam() -> None:
    """The same trip, but through the domain's own `evaluate` closure — the
    path the search actually takes. `_run_evaluator` tested directly leaves
    the closure's wiring unexercised, and that wiring is where a rename once
    left a NameError the rest of this file could not see."""
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
        script=(
            '"""contract doc"""\n'
            "import json, os\n"
            'json.dump({"valid": True, "metrics": {"score": 0.75}},\n'
            '          open(os.environ["SCIENCE_AGENT_RESULT"], "w"))\n'
        ),
        execute=_local_execution,
        baseline_code="x = 1\n",
    )

    ok, metrics, _diagnosis = domain.evaluate("x = 2\n", [0])

    assert ok is True
    assert metrics["score"] == pytest.approx(0.75)


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
        PuctEngine(completion_factory=None,            # type: ignore[arg-type]
                   evaluation_execution=_local_execution)


def test_the_engine_has_no_execution_channel_of_its_own() -> None:
    """Same shape as the model guard: the injected execution is the only way a
    candidate ever runs, and a default that executed locally would be the
    bypass the sandbox deletion exists to close."""
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import PuctEngine

    with pytest.raises(ValueError, match="injected SysOperation"):
        PuctEngine(completion_factory=lambda **_: (lambda prompt: ""),
                   evaluation_execution=None)          # type: ignore[arg-type]


def test_the_real_engines_events_actually_reach_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one seam every test used to fake: what `PuctEngine` hands `emit`.

    Every other test in this file either substitutes the engine or calls
    `absorb` with a record it built itself. Both spoke a
    ``{"createdAt", "event", "sequence"}`` envelope that no engine has produced
    since the search moved in-process — so `absorb` unwrapped a key that was
    never there, dropped every event, and a refused run came back `completed`
    with an empty tree. Found by an end-to-end run, not by this file.

    Driven through the real `PuctEngine`: no engine fake, no hand-built event.
    Only the discrimination probe is stepped past — it would refuse this
    fixture before the engine is ever built, and it is not what is under test.
    A normalisation the engine does not know makes it refuse in-thread, emit
    its own `search_finished("failed")`, and return; the assertion is that
    those events arrive.
    """
    _no_probe(monkeypatch)
    provider = PuctProgramArtifactProvider(execution=_local_execution)
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), scorecard={
        "aggregate": "weighted_sum", "constraints": [],
        "criteria": [{"id": "score", "name": "score", "direction": "maximize",
                      "weight": 1.0, "normalize": {"kind": "no_such_normalisation"},
                      "measure": {"kind": "custom_script", "scriptCas": "sha256:x",
                                  "timeoutSeconds": 30,
                                  "split": {"gateShards": 4, "rolloutShards": 2,
                                            "testShards": 2, "shardRows": 2,
                                            "seed": 1, "trainRows": None}}}],
    })
    seen: list[object] = []

    async def sink(event: object) -> None:
        seen.append(event)

    result = asyncio.run(provider.run(request, sink))

    # The refusal survives the trip: not "completed" with nothing in it.
    assert result.status == "failed", result
    assert result.error_code == "SEARCH_FAILED"
    assert [e.status for e in seen if isinstance(e, EventStatus)] == ["running", "failed"]
    assert provider.read_state(request.task_id).status == "failed"


def test_a_failed_candidate_does_not_make_a_run_unresumable() -> None:
    """A model timeout is the ordinary failure, not corruption.

    The reporter stores only text it was given, so a candidate that came back
    empty has `code_hash: null` by design. `restore_tree` treated that as a
    missing body and refused the whole resume — and since a real run of five
    candidates had one such node, it meant a run could not be continued after
    the first timeout. Found end to end, where a paused run refused to resume.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.candidates import CandidateStore
    from openjiuwen.rsi.artifact_rsi.program_opt.restore import restore_tree
    from openjiuwen.rsi.artifact_rsi.program_opt.tree import PuctTree

    import tempfile
    with tempfile.TemporaryDirectory() as root:
        store = CandidateStore(Path(root), flat=True)
        seed = "def f():\n    return 1\n"
        rows = [
            {"index": 0, "code_hash": store.put("run-1", seed), "valid": True,
             "score": 0.25, "visits": 2},
            # What a timed-out expansion actually leaves behind.
            {"index": 1, "parent_index": 0, "code_hash": None, "valid": False,
             "score": None, "visits": 1, "error": "the call returned nothing"},
        ]

        tree = restore_tree(PuctTree(), rows, store=store, search_id="run-1",
                            fallback_code=seed)

    assert [node.index for node in tree.nodes] == [0, 1]
    # The failed node keeps its identity and its verdict; only its body is empty.
    assert tree.nodes[1].program.valid is False
    assert tree.nodes[1].program.code == ""


def test_a_valid_node_whose_body_is_gone_still_refuses() -> None:
    """The tolerance above must not swallow real corruption: a node that
    scored must have the program that scored it, or the search would build a
    mutation prompt from an empty parent."""
    from openjiuwen.rsi.artifact_rsi.program_opt.candidates import CandidateStore
    from openjiuwen.rsi.artifact_rsi.program_opt.restore import RestoreError, restore_tree
    from openjiuwen.rsi.artifact_rsi.program_opt.tree import PuctTree

    import tempfile
    with tempfile.TemporaryDirectory() as root:
        store = CandidateStore(Path(root), flat=True)
        seed = "def f():\n    return 1\n"
        rows = [
            {"index": 0, "code_hash": store.put("run-1", seed), "valid": True, "score": 0.25},
            {"index": 1, "parent_index": 0, "code_hash": "sha256:" + "d" * 64,
             "valid": True, "score": 0.9},
        ]

        with pytest.raises(RestoreError, match="missing"):
            restore_tree(PuctTree(), rows, store=store, search_id="run-1", fallback_code=seed)


def test_a_refused_resume_leaves_the_paused_trees_record_alone(tmp_path: Path) -> None:
    """A resume continues one task, so it continues one durable record.

    The state used to start empty on resume, and its first write truncated
    `nodes.json` to the new attempt's nodes — so a resume that was then refused
    destroyed the paused run's tree on its way out. Seen end to end: two nodes
    went to zero, and the only copy of the work was gone.
    """
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:" + "a" * 64))
    _absorb(run, events.expanded(1, 0, 1, 0.4, True, iteration=1,
                                 code_hash="sha256:" + "b" * 64))
    _absorb(run, events.merged(1, True, "it scored better"))
    before = json.loads((Path(run.run_dir) / "nodes.json").read_text(encoding="utf-8"))
    assert len(before["nodes"]) == 2

    provider = PuctProgramArtifactProvider(execution=_local_execution)
    request = _request(tmp_path, model=None)      # fails on the model, after the state is built
    _scorecard(Path(request.run_dir))
    result = asyncio.run(provider.resume(request))

    assert result.status == "failed"
    after = json.loads((Path(run.run_dir) / "nodes.json").read_text(encoding="utf-8"))
    assert [n["node_id"] for n in after["nodes"]] == [n["node_id"] for n in before["nodes"]]
    # And the tree the caller can read is still the paused run's tree.
    assert len(provider.get_tree(request.task_id).nodes) == 2


def test_the_call_and_the_wait_agree_on_one_budget() -> None:
    """The engine's deadline is the transport's deadline, or it is fiction.

    `complete` waits `completion_timeout` (900s by default) and used to call
    `model.invoke` without saying so, leaving the client on whatever timeout it
    was configured with. Measured on a real run: the engine waited 900s by its
    own reckoning while the client gave up at 180s, so calls that needed ~550s
    — a reasoning model spends most of `max_tokens` thinking — came back as
    candidates that "returned nothing", each costing an expansion of the budget.
    """
    import asyncio as _asyncio
    from openjiuwen.rsi.artifact_rsi.program_opt.runtime import (
        DEFAULT_CALL_TIMEOUT_SECONDS,
        completion_factory_from_model,
    )

    seen: dict[str, object] = {}

    class _Recording:
        async def invoke(self, prompt: object, **kwargs: object) -> object:
            seen.update(kwargs)

            class _Usage:
                input_tokens = 1
                output_tokens = 1

            class _Message:
                content = "```python name=candidate.py\nx = 1\n```"
                usage_metadata = _Usage()

            return _Message()

    async def drive() -> None:
        loop = _asyncio.get_running_loop()
        factory = completion_factory_from_model(_Recording(), loop)

        class _Spec:
            max_tokens_per_call = 4096
            options: dict[str, object] = {}

        complete = factory(_Spec(), None, lambda: False)
        await _asyncio.to_thread(complete, "hello")

    asyncio.run(drive())

    assert seen.get("timeout") == DEFAULT_CALL_TIMEOUT_SECONDS

    # And a task that sets its own budget moves both halves together.
    seen.clear()

    async def drive_custom() -> None:
        loop = _asyncio.get_running_loop()
        factory = completion_factory_from_model(_Recording(), loop)

        class _Spec:
            max_tokens_per_call = 4096
            options = {"completion_timeout": 42.0}

        complete = factory(_Spec(), None, lambda: False)
        await _asyncio.to_thread(complete, "hello")

    asyncio.run(drive_custom())
    assert seen.get("timeout") == 42.0


def test_a_one_file_listing_still_shows_its_path() -> None:
    """The output instructions say to answer "exactly like the listing above",
    so the listing has to have a path in it.

    It did not, for the one-file case, on the reasoning that labelling a single
    file made the common case look more complicated. Measured against that:
    eight expansions in a row named the file after the function they were
    writing — `search.py` beside an untouched `candidate.py` — every reply
    merged clean, scored exactly the parent, and was recorded as a valid
    candidate that found no improvement.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.prompt import render_tree

    listing = render_tree("def solve():\n    return 1\n", "candidate.py")

    assert listing.startswith("```python name=candidate.py")


def test_a_reply_that_only_adds_files_proposed_nothing() -> None:
    """A candidate whose every edit landed in a new path is the parent.

    The evaluator imports the entrypoint; nothing in the parent references the
    new files; so the program that runs is unchanged and the candidate scores
    exactly the parent — merging clean and reading as a valid candidate. A
    whole budget can go this way with every expansion reported as a success.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.program import edits_an_existing_file

    parent = {"candidate.py": "x = 1\n"}

    # What the run actually did: wrote its program to a file of its own.
    assert edits_an_existing_file(parent, {"candidate.py": "x = 1\n",
                                           "search.py": "def search(): ...\n"}) is False
    # An ordinary edit of the entrypoint.
    assert edits_an_existing_file(parent, {"candidate.py": "x = 2\n"}) is True
    # And a helper edited beside a new file stays legitimate — the question is
    # whether anything existing moved, not whether the entrypoint did.
    grown = {"candidate.py": "x = 1\n", "helpers.py": "y = 1\n"}
    assert edits_an_existing_file(grown, {"candidate.py": "x = 1\n",
                                          "helpers.py": "y = 2\n"}) is True


def test_the_engine_refuses_to_call_an_untouched_program_a_candidate() -> None:
    """The same rule at the place that decides, driven through `_model_call`."""
    from openjiuwen.rsi.artifact_rsi.program_opt.engine import RunSpec
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import PuctEngine, _Usage

    def factory(spec, sink, should_stop):
        # A reply in the shape the run actually produced.
        return lambda prompt, on_usage=None, on_failure=None: (
            "```python name=search.py\ndef search():\n    return 2\n```"
        )

    said: list[str] = []

    class _Reporter:
        def note_empty(self, iteration): pass
        def note_failure(self, iteration, reason): said.append(reason)

    engine = PuctEngine(completion_factory=factory, evaluation_execution=_local_execution)
    spec = RunSpec(search_id="run-1", algorithm="puct", expansions=1,
                   scorecard_hash="sha256:x", scorecard={"criteria": []},
                   statement="", baseline_code="x = 1\n", script="s")

    call = engine._model_call(spec, _Usage(), _Reporter(), lambda: False, PuctTreeStub())
    code, _summary, _promise = call("prompt", 1, "x = 1\n")

    assert code == "", "an untouched program was accepted as a candidate"
    assert said and "candidate.py" in said[0] and "search.py" in said[0]


def test_the_change_sentence_is_found_where_a_one_function_reply_puts_it() -> None:
    """The prompt asks for the module docstring; a one-function program gets
    documented on the function.

    Both are the same sentence one indentation apart, and reading only the
    module's lost it on six of eight expansions in a measured run — the
    contract's `changes[].summary` degraded to "changed: candidate.py" while
    the model had written "Changed budget allocation: reduce initial random
    sampling to 40%…" two lines lower.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.program import _summary_of

    on_the_function = ('def search(objective, budget):\n'
                       '    """Reduced initial sampling to 40% of the budget."""\n'
                       '    return None\n')
    assert _summary_of(on_the_function) == "Reduced initial sampling to 40% of the budget."

    # The module's still wins when both are there — that is where the prompt
    # asks for it, and it is the one about the edit rather than about the code.
    both = ('"""Switched to Latin hypercube sampling."""\n' + on_the_function)
    assert _summary_of(both) == "Switched to Latin hypercube sampling."

    # And a reply that documents nothing still says nothing.
    assert _summary_of("def search(a, b):\n    return None\n") == ""


def test_events_from_many_workers_are_folded_one_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With more than one worker the engine emits from N threads at once, and
    neither the fold nor the delivery is safe under that.

    Mutual exclusion is the property, so it is what is measured — not "no node
    happened to be lost", which passes by luck: under the GIL a dict write is
    atomic and the losing interleaving is rare enough that three runs without
    the lock all came back green. A delivery that takes a known time makes the
    question arithmetic instead: N of them serialized cannot finish in less
    than N times that, and unserialized they overlap into roughly one.

    Driven through the provider's real sink; the only stand-in is the search.
    """
    NODES, DELAY = 12, 0.02

    class _ManyWorkers:
        def __init__(self, **_: object) -> None:
            pass

        def run(self, spec: object, emit: object, should_stop: object) -> None:
            emit(events.seeded(0, 0.25, code_hash="sha256:" + "a" * 64))
            errors: list[BaseException] = []
            ready = threading.Barrier(NODES)

            def expand(index: int) -> None:
                try:
                    ready.wait(timeout=10)          # all of them, at once
                    emit(events.expanded(index, 0, 1, 0.3 + index / 100, True,
                                         iteration=index,
                                         code_hash="sha256:" + f"{index:064x}"))
                    emit(events.merged(index, False, "measured"))
                except BaseException as error:  # noqa: BLE001 - reported below
                    errors.append(error)

            threads = [threading.Thread(target=expand, args=(i,))
                       for i in range(1, NODES + 1)]
            started = time.monotonic()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.elapsed = time.monotonic() - started
            assert not errors, f"the fold raised under concurrency: {errors[:3]}"
            emit(events.search_finished("succeeded", 1, NODES))

    engines: list[_ManyWorkers] = []

    def build(**kwargs: object) -> _ManyWorkers:
        engines.append(_ManyWorkers(**kwargs))
        return engines[-1]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine", build)
    provider = PuctProgramArtifactProvider(execution=_local_execution)
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    async def sink(event: object) -> None:
        await asyncio.sleep(DELAY)

    result = asyncio.run(provider.run(request, sink))

    assert result.status == "completed"
    # Each expansion delivers one EventNode, so serialized they cost at least
    # their own delay each. Overlapping, twelve of them cost about one.
    assert engines[0].elapsed > NODES * DELAY * 0.6, (
        f"{NODES} deliveries took {engines[0].elapsed:.3f}s — they overlapped, "
        "so the fold is not serialized"
    )
    # And nothing was lost on the way.
    persisted = json.loads((Path(request.run_dir) / "nodes.json").read_text(encoding="utf-8"))
    indices = sorted(int(n["node_id"].rsplit(":", 1)[-1]) for n in persisted["nodes"])
    assert indices == list(range(NODES + 1)), f"{len(indices)} of {NODES + 1} nodes survived"


def test_a_task_may_ask_for_more_than_one_worker(tmp_path: Path) -> None:
    """Serial was the only safe setting while the fold had no lock; it is a
    default now, not a ceiling. Bounded, because each worker is a model call
    and a sandbox evaluation in flight."""
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)

    _scorecard(Path(request.run_dir))
    assert provider._spec_for(request, resumed=False).workers == 1

    _scorecard(Path(request.run_dir), workers=4)
    assert provider._spec_for(request, resumed=False).workers == 4

    _scorecard(Path(request.run_dir), workers=200)
    assert provider._spec_for(request, resumed=False).workers == 8      # capped

    _scorecard(Path(request.run_dir), workers="lots")
    assert provider._spec_for(request, resumed=False).workers == 1      # not a number


def test_a_card_builds_one_sandbox_per_evaluation_and_hands_each_back() -> None:
    """A sandbox of its own for every evaluation, created and reclaimed here.

    The isolation a scratch directory cannot give: a candidate that gets past
    the AST gate and writes outside its own directory reaches only a container
    about to be discarded. And since nothing else knows these containers exist
    — AgentServer registered one card, not the dozens this makes — leaving one
    behind is a leak nobody else can find, so the release is part of the same
    call.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt import execution as execution_module

    built: list[str] = []
    released: list[tuple[str, str]] = []

    class _Card:
        id = "rsi-card"

        def __init__(self) -> None:
            self.gateway_config = type("G", (), {
                "isolation": type("I", (), {"container_scope": None, "custom_id": None})(),
                "launcher_config": type("L", (), {"on_stop": "delete"})(),
            })()

        def model_copy(self, deep: bool = False) -> "_Card":
            return _Card()

    class _Operation:
        def __init__(self, card: object) -> None:
            self.isolation_key_template = f"key-{card.gateway_config.isolation.custom_id}"
            built.append(self.isolation_key_template)

        def fs(self): return _Fs()
        def shell(self): return _Shell()

    class _Fs:
        async def write_file(self, path, text, **kw):
            return type("R", (), {"code": 0, "message": "success"})()

        async def read_file(self, path):
            return type("R", (), {"data": type("D", (), {"content": "{}"})()})()

    class _Shell:
        async def execute_cmd(self, command, **kw):
            return type("R", (), {"data": type("D", (), {
                "exit_code": 0, "stdout": "", "stderr": ""})()})()

    class _Client:
        @staticmethod
        async def release(key, on_stop="delete"):
            released.append((key, on_stop))

    import sys as _sys, types as _types
    fake_core = _types.ModuleType("openjiuwen.core.sys_operation")
    fake_core.SysOperation = _Operation
    fake_config = _types.ModuleType("openjiuwen.core.sys_operation.config")
    fake_config.ContainerScope = type("CS", (), {"CUSTOM": "custom"})
    fake_gw = _types.ModuleType(
        "openjiuwen.core.sys_operation.sandbox.gateway.gateway_client")
    fake_gw.SandboxGatewayClient = _Client
    saved = {name: _sys.modules.get(name) for name in
             (fake_core.__name__, fake_config.__name__, fake_gw.__name__)}
    _sys.modules.update({fake_core.__name__: fake_core, fake_config.__name__: fake_config,
                         fake_gw.__name__: fake_gw})
    try:
        async def drive() -> None:
            loop = asyncio.get_running_loop()
            execute = execution_module.execution_from_sandbox_card(_Card(), loop)
            for _ in range(3):
                await asyncio.to_thread(
                    execute, {"candidate.py": "x = 1\n"}, ["python", "candidate.py"],
                    {}, 30.0, "result.json")

        asyncio.run(drive())
    finally:
        for name, module in saved.items():
            if module is None:
                _sys.modules.pop(name, None)
            else:
                _sys.modules[name] = module

    assert len(built) == 3, f"expected one sandbox per evaluation, built {built}"
    assert len(set(built)) == 3, f"the three evaluations shared a container: {built}"
    # Every one of them handed back, under the key it was created with.
    assert [key for key, _ in released] == built
    assert {stop for _, stop in released} == {"delete"}


def test_a_card_beats_an_instance_and_packages_become_a_prelude(tmp_path: Path) -> None:
    """A caller that supplied a card asked for the stricter thing, so the card
    wins. And each of those containers starts empty, so what the run was
    promised has to be installed into every one of them — with a shared
    sandbox that happens once at run start and the prelude stays empty."""
    seen: dict[str, object] = {}

    def recording(card, loop, prelude=()):
        seen["card"], seen["prelude"] = card, prelude
        return _local_execution

    provider = PuctProgramArtifactProvider(sys_operation=object(), sandbox_card="the-card")
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), packages=["numpy", "scipy==1.18.1"])
    spec = provider._spec_for(request, resumed=False)

    from openjiuwen.rsi.artifact_rsi.program_opt import puct_provider as module
    original = module.execution_from_sandbox_card
    module.execution_from_sandbox_card = recording
    try:
        provider._execution_for(spec, loop=None)
    finally:
        module.execution_from_sandbox_card = original

    assert seen["card"] == "the-card", "the instance was used even though a card was given"
    assert list(seen["prelude"]) == [["python", "-m", "pip", "install", "--no-input",
                                      "--disable-pip-version-check", "numpy", "scipy==1.18.1"]]

    # No packages, nothing to reinstall.
    _scorecard(Path(request.run_dir))
    module.execution_from_sandbox_card = recording
    try:
        provider._execution_for(provider._spec_for(request, resumed=False), loop=None)
    finally:
        module.execution_from_sandbox_card = original
    assert list(seen["prelude"]) == []


# -- task-owned prompt wording ---------------------------------------------------


def test_a_tasks_mutation_template_reaches_the_real_prompt(tmp_path: Path) -> None:
    """Different tasks need differently assembled prompts, so the wording is
    task data (`run_dir/prompts/mutation.md`) rendered over the framework's
    slots. Driven through the real domain closure, not a copy of it — and the
    template deliberately contains JSON braces, which is why the syntax is
    `${...}` (`string.Template`) and not `str.format`."""
    from openjiuwen.rsi.artifact_rsi.program_opt.program import Program, program_id
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
        execute=_local_execution,
        statement="pack the bins tighter",
        baseline_code="def pack():\n    return []\n",
        mutation_template=(
            "TASK SAYS: ${statement}\n"
            'a JSON example the model should copy: {"bins": [1, 2]}\n'
            "PROGRAM:\n${parent_code}\n${reply_format}\n"
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


def test_the_prompt_never_argues_with_the_tasks_own_contract() -> None:
    """The built-in template used to name `train_and_predict` as an example of
    a function "the evaluator never asked for". For any task whose evaluator
    asks for exactly that — one of the most ordinary interfaces there is — the
    prompt said define it and don't define it, on the same screen. Seen in a
    real run's prompt; the model happened to ignore the contradiction.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.prompt import mutation_prompt

    text = mutation_prompt(
        statement="fit the curve",
        scorecard={"criteria": []},
        parent_code="def train_and_predict(train, test):\n    return []\n",
        entrypoint="candidate.py",
        parent_score=0.1,
        best_score=None,
        recent=(),
        script_contract="Your program must define train_and_predict(train, test).",
        feedback="",
        template="",
    )

    # The principle survives; the example that could contradict the task does not.
    assert "An interface that does not match scores zero" in text
    assert "never asked for (`train_and_predict`" not in text


def test_the_model_is_told_what_it_has_to_beat() -> None:
    """`best_score` is the run's selection pressure, and it was wired to None.

    Every mutation prompt in a real run read "Best so far: not measured yet",
    for the whole budget. Driven through the real `make_propose` closure — the
    thing that actually builds the prompt — rather than by calling
    `mutation_prompt` with a number a test chose.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.program import program_id
    from openjiuwen.rsi.artifact_rsi.program_opt.search import make_propose
    from openjiuwen.rsi.artifact_rsi.program_opt.tree import Node, PuctTree
    from openjiuwen.rsi.artifact_rsi.program_opt.program import Program

    def program(code: str, score: float) -> Node:
        return Node(index=0, parent_index=None,
                    program=Program(program_id(code), 0, None, code, "", {}, True, ""),
                    score=score, num_visits=1, promise=None)

    tree = PuctTree()
    tree.nodes.append(program("x = 1\n", 0.25))
    tree.nodes.append(program("x = 2\n", 0.75))     # the one to beat
    tree.nodes[1].index = 1

    seen: list[str] = []
    domain = _domain_recording_prompts(seen)
    propose = make_propose(tree, lambda prompt, i, code: ("x = 3\n", "", None), domain)

    propose("", object(), "", 0.0)

    assert seen, "the closure never built a prompt"
    assert "0.75" in seen[0] or "0.7500" in seen[0], seen[0]


def _domain_recording_prompts(seen: list[str]):
    """A Domain whose `prompt` records the best score it was handed."""
    from openjiuwen.rsi.artifact_rsi.program_opt.domain import Domain

    def prompt(program: object, best_score: float | None = None) -> str:
        rendered = f"parent + best={best_score}"
        seen.append(rendered)
        return rendered

    return Domain(
        name="recorder", entrypoint="candidate.py", metric_key="score",
        metric_better="higher", initial_program="x = 1\n", initial_summary="",
        evaluate=lambda code, shards: (True, {"score": 1.0}, ""),
        reward=lambda metrics: 1.0, prompt=prompt,
        task_prompt=lambda shard: "", test_shards=(), data_summary={},
    )


def test_a_template_that_never_says_how_to_reply_is_refused_at_load(tmp_path: Path) -> None:
    """The output protocol is the one section a task cannot silently drop.

    A template may say anything it likes — except leave out how the model must
    answer. The reader on the other side expects one shape; a reply in another
    is treated as the program itself (deliberately, so junk still becomes a
    node), so it lands in the candidate file as a syntax error, scores -inf,
    and the next expansion does the same. The whole budget burns with every
    step reporting success. Refused here, where it is still a sentence.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_provider import _prompt_templates

    prompts = tmp_path / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "mutation.md").write_text(
        "Rewrite ${parent_code} to score better.", encoding="utf-8")

    with pytest.raises(ValueError) as refusal:
        _prompt_templates(tmp_path)

    assert "${reply_format}" in str(refusal.value)
    assert "budget" in str(refusal.value)


def test_the_instructions_and_the_reader_are_one_thing() -> None:
    """Every shipped protocol round-trips: a reply written to its own
    instructions is read back by its own parser.

    This is the property the pairing exists for — the two halves used to live
    in different modules and could drift apart with nothing failing.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.reply_format import format_for

    program = "def solve():\n    return 42\n"
    replies = {
        "files": f"```python name=candidate.py\n{program}```",
        "tagged": f"<PROGRAM>\n{program}</PROGRAM>\n<CHANGE_SUMMARY>made it 42</CHANGE_SUMMARY>",
    }
    for name, reply in replies.items():
        shape = format_for(name)
        assert shape.carries_program(reply), name
        files, summary = shape.parse(reply, {"candidate.py": "x = 1\n"}, "candidate.py")
        assert files["candidate.py"].strip() == program.strip(), name
    # And the summary the tagged shape asks for is the summary it reads.
    _files, summary = format_for("tagged").parse(replies["tagged"], {}, "candidate.py")
    assert summary == "made it 42"


def test_a_reply_in_the_other_protocol_is_not_mistaken_for_a_program() -> None:
    """The failure this whole seam exists to stop, pinned from the other side:
    `tagged` output handed to the `files` reader used to become the candidate,
    tags and all."""
    from openjiuwen.rsi.artifact_rsi.program_opt.reply_format import format_for

    tagged = "<PROGRAM>\ndef solve():\n    return 42\n</PROGRAM>"
    files, _summary = format_for("files").parse(tagged, {"candidate.py": "x = 1\n"}, "candidate.py")

    # It still becomes *something* — dropping it would shrink the rank
    # denominator — but the tags prove it was never the declared shape, which
    # is why the load-time refusal above is the guard that matters.
    assert "<PROGRAM>" in files["candidate.py"]
    assert format_for("tagged").parse(tagged, {"candidate.py": "x = 1\n"},
                                      "candidate.py")[0]["candidate.py"] == "def solve():\n    return 42"


def test_an_unknown_protocol_is_refused_by_name_before_the_budget(tmp_path: Path) -> None:
    from openjiuwen.rsi.artifact_rsi.program_opt.reply_format import ReplyFormatError, format_for

    with pytest.raises(ReplyFormatError) as refusal:
        format_for("search_replace_diff")
    assert "files" in str(refusal.value) and "tagged" in str(refusal.value)

    # And the name reaches the spec from the task's own scorecard.
    provider = PuctProgramArtifactProvider()
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir), reply_format="tagged")
    assert provider._spec_for(request, resumed=False).reply_format == "tagged"


def test_the_engine_reads_replies_with_the_protocol_the_spec_named() -> None:
    """The dispatch itself, driven through the engine's own closure.

    The tests above prove each protocol is internally consistent and that the
    name reaches the spec — neither notices if the engine ignores the name and
    reads every reply as `files`. This one hands the real `_model_call` closure
    a `tagged` reply under a `tagged` spec and asks what the candidate became.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.engine import RunSpec
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import PuctEngine, _Usage

    reply = ("<PROGRAM>\ndef solve():\n    return 42\n</PROGRAM>\n"
             "<CHANGE_SUMMARY>answered 42</CHANGE_SUMMARY>")

    def factory(spec, sink, should_stop):
        def complete(prompt, on_usage=None, on_failure=None):
            return reply
        return complete

    engine = PuctEngine(completion_factory=factory, evaluation_execution=_local_execution)
    spec = RunSpec(
        search_id="run-1", algorithm="puct", expansions=1,
        scorecard_hash="sha256:x", scorecard={"criteria": []},
        statement="", baseline_code="x = 1\n", script="s",
        reply_format="tagged",
    )

    class _Reporter:
        def note_empty(self, iteration): pass
        def note_failure(self, iteration, reason): pass

    call = engine._model_call(spec, _Usage(), _Reporter(), lambda: False, PuctTreeStub())
    code, summary, _promise = call("prompt", 1, "x = 1\n")

    assert "def solve()" in code and "<PROGRAM>" not in code
    assert summary == "answered 42"


class PuctTreeStub:
    """Only what `_model_call` touches when the search is not stopping."""

    candidate_limit = None


def test_the_prompt_shows_the_parent_in_the_shape_it_asks_for() -> None:
    """The listing is part of the protocol, not decoration.

    The instructions say to answer "exactly like the listing above", so a
    prompt that renders the parent as a labelled fence while asking for
    `<PROGRAM>` tags is two instructions in conflict — and the demonstration
    wins. Measured on a real `tagged` run before this: eight replies in a row
    came back as labelled fences, the tagged reader saw none of them, and
    every expansion was recorded as an empty draw with the run finishing on
    its seed.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.prompt import mutation_prompt
    from openjiuwen.rsi.artifact_rsi.program_opt.reply_format import format_for

    def rendered(name: str) -> str:
        return mutation_prompt(
            statement="make it better", scorecard={"criteria": []},
            parent_code="def solve():\n    return 1\n", parent_score=0.4,
            best_score=0.5, recent=(), script_contract="define solve()",
            reply_format=format_for(name), feedback="", template="",
        )

    files_prompt, tagged_prompt = rendered("files"), rendered("tagged")

    assert "```python name=candidate.py" in files_prompt
    assert "<PROGRAM>" not in files_prompt

    assert "<PROGRAM>\ndef solve():" in tagged_prompt
    assert "name=candidate.py" not in tagged_prompt


def test_the_engine_tells_the_domain_which_protocol_to_render() -> None:
    """`mutation_prompt` honouring the protocol is half the wire; the domain
    that builds the prompt has to be told which one, by the engine.

    It was not. `script_domain` grew the parameter and the engine never passed
    it, so a `tagged` run still rendered its parent as a labelled fence and the
    model still answered in fences — eight expansions, all read as empty. A
    test that calls `script_domain` itself passes either way; this one records
    what the engine hands its domain factory, which is the thing that was
    wrong.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.engine import RunSpec
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import PuctEngine
    from openjiuwen.rsi.artifact_rsi.program_opt.script_domain import ScriptError

    seen: dict[str, object] = {}

    def recording_factory(**kwargs: object) -> object:
        seen.update(kwargs)
        # The engine turns this into a refusal and returns; the arguments it
        # was called with are the whole of what is under test.
        raise ScriptError("stop here")

    engine = PuctEngine(completion_factory=lambda *a, **k: (lambda p, **kw: ""),
                        evaluation_execution=_local_execution,
                        domain_factory=recording_factory)
    spec = RunSpec(
        search_id="run-1", algorithm="puct", expansions=1,
        scorecard_hash="sha256:x",
        scorecard={"criteria": [{"id": "score", "normalize": {"kind": "identity"},
                                 "measure": {"kind": "custom_script"}}]},
        statement="", baseline_code="x = 1\n", script='"""doc"""\n',
        reply_format="tagged",
    )

    engine.run(spec, lambda event: None, lambda: False)

    assert seen.get("reply_format") == "tagged", (
        "the engine built its domain without saying which protocol the reply "
        f"will be read with; it passed {sorted(seen)}"
    )


def test_a_one_file_protocol_refuses_a_program_that_is_a_tree() -> None:
    """`tagged` has nowhere to say which file a block belongs to, so it shows
    the entrypoint and accepts the entrypoint.

    Pairing it with a tree does not fail — it hides every other file from the
    model, which is then asked to improve a program it can only see part of.
    Measured: a two-file run whose helper held the bug never showed the helper,
    and the winner inlined around it, having inferred the fault from an import
    line and a score. Refused before the budget instead, naming both files.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.engine import RunSpec
    from openjiuwen.rsi.artifact_rsi.program_opt.program import bundle
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import _refuse_unrunnable

    tree = bundle({"candidate.py": "from helper import f\n", "helper.py": "def f(): ...\n"})
    spec = RunSpec(
        search_id="run-1", algorithm="puct", expansions=1, scorecard_hash="sha256:x",
        scorecard={"criteria": [{"id": "score", "normalize": {"kind": "identity"},
                                 "measure": {"kind": "custom_script"}}]},
        statement="", baseline_code=tree, script='"""doc"""\n', reply_format="tagged",
    )

    with pytest.raises(Exception) as refusal:
        _refuse_unrunnable(spec, _local_execution)
    said = str(refusal.value)
    assert "tagged" in said and "helper.py" in said and "candidate.py" in said

    # The same tree under `files` is exactly what that format is for.
    _refuse_unrunnable(RunSpec(**{**spec.__dict__, "reply_format": "files"}),
                       _local_execution)


def test_an_unknown_placeholder_is_refused_by_name_at_load(tmp_path: Path) -> None:
    """`safe_substitute` would leave `${statment}` in the prompt as literal
    text, and the model would optimise against a prompt with a hole in it for
    the whole budget. The load is the one moment the mistake can still be a
    sentence."""
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_provider import _prompt_templates

    prompts = tmp_path / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "mutation.md").write_text(
        "goal: ${statment}\n${reply_format}", encoding="utf-8")

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
    (prompts / "mutation.md").write_text(
        "M ${statement}\n${reply_format}", encoding="utf-8")
    (prompts / "repair.md").write_text("R ${error}\n${code}", encoding="utf-8")
    (prompts / "prior.md").write_text("${prompt}\nRate it.", encoding="utf-8")

    spec = provider._spec_for(request, resumed=False)

    assert spec.mutation_template == "M ${statement}\n${reply_format}"
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


def test_read_report_survives_schema_drift_in_the_persisted_file(tmp_path: Path) -> None:
    """A report written by another build may carry keys this one no longer
    has — this branch has removed persisted fields twice already. Drift must
    degrade to a best-effort report, not turn every future read into a
    TypeError."""
    import json as jsonlib

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    state_module.register_run_dir("task-001", run_dir)
    (run_dir / "report.json").write_text(jsonlib.dumps({
        "task_id": "task-001",
        "status": "completed",
        "best_node_id": "artifact:task-001:node:1",
        "usage": {"tokens": {"input": 1, "output": 2}},        # removed field
        "artifact_index": [
            {"artifact_id": "A-program:task-001:abcd", "node_id": None,
             "name": "candidate-1", "kind": "program_snapshot",
             "path": str(run_dir / "candidates" / "abcd"), "sha256": "abcd",
             "download_url": None,
             "legacy_field_nobody_remembers": True},           # unknown key
            "not-a-mapping",                                    # corrupt entry
        ],
        "summary": None,
    }), encoding="utf-8")

    report = PuctProgramArtifactProvider().read_report("task-001")

    assert report.status == "completed"
    assert len(report.artifact_index) == 1                     # corrupt entry skipped
    assert report.artifact_index[0].artifact_id == "A-program:task-001:abcd"


def test_get_tree_survives_schema_drift_in_the_persisted_file(tmp_path: Path) -> None:
    """Same hole `read_report` had, in the tree's clothing: `RsiTreeNode(**raw)`
    turned one unknown key into a TypeError that took the whole tree — and
    `get_tree` is a correctness channel, the one AgentServer's restart
    compensation reads."""
    import json as jsonlib

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    state_module.register_run_dir("task-001", run_dir)
    (run_dir / "nodes.json").write_text(jsonlib.dumps({"nodes": [
        {"node_id": "artifact:task-001:node:0", "iteration": 0, "parent_id": None,
         "type": "root", "adopted": True, "score": 0.25, "summary": "seed",
         "snapshot_artifact_id": None, "reason": None, "failure_class": None,
         "changes": [], "extra": {"program": {}},
         "field_from_the_future": 1},                       # unknown key
        {"node_id": "artifact:task-001:node:1", "iteration": 1,
         "parent_id": "artifact:task-001:node:0", "type": "adopted",
         "adopted": True, "score": 0.4,
         "changes": [{"group": "program", "operation": "modify",
                      "summary": "…", "obsolete_change_key": True}],
         "extra": {}},                                      # 缺了若干可选键
        "not-a-mapping",
    ]}), encoding="utf-8")

    tree = PuctProgramArtifactProvider().get_tree("task-001")

    assert len(tree.nodes) == 2                             # corrupt entry skipped
    assert tree.depth == 1
    assert tree.nodes[1].parent_id == "artifact:task-001:node:0"
    assert tree.nodes[1].changes[0].operation == "modify"


def test_node_extra_carries_the_contracts_program_object(tmp_path: Path) -> None:
    """`extra["program"]` is `ProgramNodeExtra`: nine keys, always present,
    null semantics per field — a reader that must probe which keys exist has
    been handed a different structure per node."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aaaa"))
    _absorb(run, events.selected(0, [{"nodeIndex": 0, "visits": 2}],
                                 rank_score=0.5, puct=0.9))
    _absorb(run, events.expanded(1, 0, 1, 0.4, True, change_summary="tighten the loop",
                                 code_hash="sha256:bbbb", iteration=1))
    _absorb(run, events.evaluated(1, 0.4, {"score": 0.4},
                                  gate_score=0.42, rollout_score=0.38))
    _absorb(run, events.merged(1, True, reason="提升"))
    _absorb(run, events.expanded(2, 1, 2, None, False, error="IndexError at line 3",
                                 code_hash="sha256:cccc", iteration=2))
    _absorb(run, events.merged(2, False, reason="没跑起来", category="candidate-failed"))

    keys = {"logical_kind", "candidate_index", "source_ref", "program_path",
            "parent_index", "evaluation", "puct", "artifacts", "error"}
    tree = state_module.read_tree_file("task-001")
    root, adopted, rejected = tree.nodes

    for node in tree.nodes:
        assert set(node.extra["program"]) == keys, node.node_id

    r = root.extra["program"]
    assert r["logical_kind"] == "root" and r["candidate_index"] is None
    assert r["source_ref"] == "sha256:aaaa" and r["parent_index"] is None
    assert r["evaluation"] == {"valid": True, "gate": 0.25}
    assert r["puct"] == {"visits": 2, "rank": 0.5, "value": 0.9}

    a = adopted.extra["program"]
    assert a["logical_kind"] == "adopted" and a["candidate_index"] == 1
    assert a["parent_index"] == 0
    assert a["evaluation"]["gate"] == 0.42 and a["evaluation"]["rollout"] == 0.38
    assert a["error"] is None and len(a["artifacts"]) == 1

    x = rejected.extra["program"]
    assert x["logical_kind"] == "rejected"
    assert x["evaluation"] == {"valid": False, "score": None}
    assert x["error"]["message"] == "IndexError at line 3"
    assert x["error"]["class"] is not None


def test_adopted_means_the_current_version_chain_not_merge_history(tmp_path: Path) -> None:
    """The contract reads 当前版本链 — one chain at a time. Merge acceptance
    used to set the flag permanently, so adopted nodes accumulated as best
    moved. History keeps its own fields: `type` and `logical_kind` still say
    "adopted" for a node the chain has since left."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aaaa"))
    _absorb(run, events.expanded(1, 0, 1, 0.4, True, code_hash="sha256:bbbb", iteration=1))
    emitted_first = _absorb(run, events.merged(1, True, reason="提升"))
    # A sibling from the root overtakes: the chain becomes 0 → 2.
    _absorb(run, events.expanded(2, 0, 1, 0.6, True, code_hash="sha256:cccc", iteration=2))
    emitted = _absorb(run, events.merged(2, True, reason="更高"))

    tree = state_module.read_tree_file("task-001")
    flags = {n.node_id.rsplit(":", 1)[-1]: n.adopted for n in tree.nodes}
    assert flags == {"0": True, "1": False, "2": True}

    overtaken = next(n for n in tree.nodes if n.node_id.endswith(":1"))
    assert overtaken.type == "adopted"                             # 历史裁决保留
    assert overtaken.extra["program"]["logical_kind"] == "adopted"

    # The delta channel heard about the old chain going stale, not only the
    # new winner — otherwise a consumer holds an adopted=True node the tree
    # no longer claims.
    pushed = [e.node.node_id for e in emitted if isinstance(e, EventNode)]
    assert any(node_id.endswith(":1") for node_id in pushed)
    assert any(node_id.endswith(":2") for node_id in pushed)


def test_summary_carries_the_change_and_how_it_fared(tmp_path: Path) -> None:
    """The contract's `summary` is 修改和评测结果摘要 — both halves. Composed
    mechanically at the merge from numbers the events already carry; no model
    is asked to restate what the fold can format. Recomposition is from the
    pristine text in `changes[0].summary`, so a replayed merge yields the same
    line instead of a second appended verdict."""
    run = _state(tmp_path)
    _absorb(run, events.seeded(0, 0.25, code_hash="sha256:aaaa"))
    _absorb(run, events.expanded(1, 0, 1, 0.4, True, change_summary="tighten the loop",
                                 code_hash="sha256:bbbb", iteration=1))
    _absorb(run, events.evaluated(1, 0.4, {"score": 0.4}, gate_score=0.42))
    _absorb(run, events.merged(1, True, reason="提升"))
    _absorb(run, events.expanded(2, 0, 1, 0.1, True, change_summary="a worse idea",
                                 code_hash="sha256:cccc", iteration=2))
    _absorb(run, events.evaluated(2, 0.1, {"score": 0.1}, gate_score=0.10))
    _absorb(run, events.merged(2, False, reason="低于最优"))
    _absorb(run, events.expanded(3, 0, 1, None, False,
                                 change_summary="an idea that crashes",
                                 error="IndexError at line 3\nlong traceback…",
                                 code_hash="sha256:dddd", iteration=3))
    _absorb(run, events.merged(3, False, reason="没跑起来", category="candidate-failed"))

    tree = state_module.read_tree_file("task-001")
    by = {n.node_id.rsplit(":", 1)[-1]: n for n in tree.nodes}

    assert by["0"].summary == "the starting program — gate 0.2500 (baseline)"
    assert by["1"].summary == \
        "tighten the loop — gate 0.4200 (baseline 0.2500), adopted"
    assert by["2"].summary == \
        "a worse idea — gate 0.1000, below the current best 0.4000, not adopted"
    # A crash shows the first diagnosis line, not the whole traceback.
    assert by["3"].summary == "an idea that crashes — did not run: IndexError at line 3"

    # Replay: the merge arrives twice, the line stays one verdict long.
    _absorb(run, events.merged(1, True, reason="提升"))
    tree = state_module.read_tree_file("task-001")
    replayed = next(n for n in tree.nodes if n.node_id.endswith(":1"))
    assert replayed.summary == \
        "tighten the loop — gate 0.4200 (baseline 0.2500), adopted"


def test_a_raising_callback_does_not_fail_the_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract's own words: a callback exception is an observability-
    channel fault; it must not roll back persisted results or mark the task
    failed — AgentServer compensates through the query interfaces. Before the
    fix, the exception propagated into the engine and came back out as
    ENGINE_ERROR: the search died of a broken telescope."""
    class _Finishing:
        def __init__(self, **_: object) -> None:
            pass

        def run(self, spec: object, emit: object, should_stop: object) -> None:
            emit(events.seeded(0, 0.25, code_hash="sha256:aa"))  # type: ignore[operator]
            emit(events.search_finished("succeeded", 0, 1))  # type: ignore[operator]

    _no_probe(monkeypatch)
    monkeypatch.setattr(
        "openjiuwen.rsi.artifact_rsi.program_opt.puct_provider.PuctEngine",
        _Finishing,
    )
    provider = PuctProgramArtifactProvider(execution=_local_execution)
    request = _request(tmp_path)
    _scorecard(Path(request.run_dir))

    async def broken_sink(event: object) -> None:
        raise RuntimeError("the queue is gone")

    result = asyncio.run(provider.run(request, broken_sink))

    assert result.status == "completed"
    assert result.error_code is None
    # And the durable snapshots survived the broken telescope.
    assert provider.read_state(request.task_id).status == "completed"
