"""Tests that _execute_once kills the entire process tree on timeout,
and that cancel() / _cleanup_active_process handle interrupted tasks."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from unittest.mock import patch

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    ImplementedVariant,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.agent import (
    ExperimentExecutionAgent,
    _kill_process_tree,
)

_SLEEP_SCRIPT = textwrap.dedent("""\
    import argparse
    import time
    p = argparse.ArgumentParser()
    p.add_argument("--method")
    p.add_argument("--output")
    p.parse_args()
    time.sleep(300)
""")

_CHILD_SPAWNER_SCRIPT = textwrap.dedent("""\
    import argparse
    import subprocess
    import sys
    import time
    p = argparse.ArgumentParser()
    p.add_argument("--method")
    p.add_argument("--output")
    args = p.parse_args()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    time.sleep(300)
""")


def _run(tmp_path: Path, script: str, timeout: int = 3):
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "run.py").write_text(script, encoding="utf-8")
    logs = tmp_path / "logs"
    results = tmp_path / "results"
    logs.mkdir()
    results.mkdir()
    agent = ExperimentExecutionAgent({})
    variant = ImplementedVariant(
        name="proposed",
        invocation=[sys.executable, "run.py", "--method", "proposed"],
    )
    return agent, agent._run_variant(
        variant,
        code_dir=code_dir,
        logs=logs,
        results=results,
        timeout=timeout,
        max_transient_retries=0,
    )


def test_timeout_kills_process_tree(tmp_path: Path):
    """When a subprocess times out, _kill_process_tree is called with its PID."""
    killed_pids: list[int] = []
    original_kill = _kill_process_tree

    def spy_kill(pid: int) -> None:
        killed_pids.append(pid)
        original_kill(pid)

    with patch(
        "openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.agent._kill_process_tree",
        side_effect=spy_kill,
    ):
        agent, (result, run) = _run(tmp_path, _SLEEP_SCRIPT, timeout=2)

    assert result.process_status == "failed"
    assert run.timed_out
    assert len(killed_pids) == 1, f"expected exactly 1 kill call, got {killed_pids}"
    assert agent._active_process is None


def test_active_process_cleared_after_normal_exit(tmp_path: Path):
    """After a successful run, _active_process is None."""
    ok_script = textwrap.dedent("""\
        import argparse, json
        from pathlib import Path
        p = argparse.ArgumentParser()
        p.add_argument("--method")
        p.add_argument("--output")
        args = p.parse_args()
        Path(args.output).write_text(json.dumps({
            "method": args.method, "n_questions": 1,
            "model_call_count": 1, "per_question": [{"id": "one", "correct": True}],
        }))
    """)
    agent, (result, _) = _run(tmp_path, ok_script)
    assert result.process_status == "completed"
    assert agent._active_process is None


def test_cleanup_active_process_noop_when_none():
    """_cleanup_active_process is safe when no process is tracked."""
    agent = ExperimentExecutionAgent({})
    assert agent._active_process is None
    agent._cleanup_active_process()
    assert agent._active_process is None


def test_cleanup_active_process_kills_running():
    """_cleanup_active_process terminates a still-running tracked process."""
    agent = ExperimentExecutionAgent({})
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
    )
    agent._active_process = proc
    agent._cleanup_active_process()
    assert agent._active_process is None
    assert proc.poll() is not None


def test_popen_uses_process_group(tmp_path: Path):
    """_execute_once creates the subprocess in a new process group."""
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    ok_script = textwrap.dedent("""\
        import argparse, json
        from pathlib import Path
        p = argparse.ArgumentParser()
        p.add_argument("--method")
        p.add_argument("--output")
        args = p.parse_args()
        Path(args.output).write_text(json.dumps({
            "method": args.method, "n_questions": 1,
            "model_call_count": 1, "per_question": [{"id": "one", "correct": True}],
        }))
    """)
    (code_dir / "run.py").write_text(ok_script, encoding="utf-8")
    metrics_path = tmp_path / "results" / "proposed.metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    agent = ExperimentExecutionAgent({})

    popen_calls: list[dict] = []
    original_popen = subprocess.Popen

    class SpyPopen(original_popen):
        def __init__(self, *args, **kwargs):
            popen_calls.append(kwargs.copy())
            super().__init__(*args, **kwargs)

    with patch("subprocess.Popen", SpyPopen):
        agent._execute_once(
            [sys.executable, "run.py", "--method", "proposed", "--output", str(metrics_path)],
            code_dir=code_dir,
            timeout=10,
            metrics_path=metrics_path,
        )

    assert len(popen_calls) >= 1
    first = popen_calls[0]
    if sys.platform == "win32":
        assert first.get("creationflags", 0) & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert first.get("start_new_session") is True


def test_cancel_kills_running_subprocess_from_another_thread(tmp_path: Path):
    """Simulates the frontend pause scenario: agent.run() is in a worker
    thread, and cancel() is called from the asyncio thread."""
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "run.py").write_text(_SLEEP_SCRIPT, encoding="utf-8")
    logs = tmp_path / "logs"
    results = tmp_path / "results"
    logs.mkdir()
    results.mkdir()
    agent = ExperimentExecutionAgent({})
    variant = ImplementedVariant(
        name="proposed",
        invocation=[sys.executable, "run.py", "--method", "proposed"],
    )
    errors: list[Exception] = []

    def run_agent():
        try:
            agent._run_variant(
                variant,
                code_dir=code_dir,
                logs=logs,
                results=results,
                timeout=300,
                max_transient_retries=0,
            )
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_agent)
    worker.start()
    # Wait until the subprocess is actually running
    deadline = time.monotonic() + 10
    while agent._active_process is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert agent._active_process is not None, "subprocess never started"

    # Simulate frontend pause: cancel from the main thread
    agent.cancel()
    worker.join(timeout=20)
    assert not worker.is_alive(), "worker thread did not finish"
    assert agent._cancel_generation >= 1
    assert agent._active_process is None


def test_cancel_before_run_still_detected(tmp_path: Path):
    """Covers the race window the reviewer identified: cancel() fires before
    the worker thread enters run().  With the generation approach the
    cancellation is never lost."""
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "run.py").write_text(_SLEEP_SCRIPT, encoding="utf-8")
    logs = tmp_path / "logs"
    results = tmp_path / "results"
    logs.mkdir()
    results.mkdir()
    agent = ExperimentExecutionAgent({})
    variant = ImplementedVariant(
        name="proposed",
        invocation=[sys.executable, "run.py", "--method", "proposed"],
    )

    # Cancel BEFORE _run_variant is called — simulates "cancel arrived while
    # the worker thread was still queued in the thread pool".
    cancel_gen = agent._cancel_generation
    agent.cancel()

    import pytest

    with pytest.raises(RuntimeError, match="cancelled"):
        agent._run_variant(
            variant,
            code_dir=code_dir,
            logs=logs,
            results=results,
            timeout=300,
            max_transient_retries=0,
            cancel_generation=cancel_gen,
        )
