"""Regression coverage for argument grouping, scoped progress and CLI output boundaries."""

import asyncio
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.reward import evidence_agent, pipeline, progress
from openjiuwen.agent_evolving.reward.cli_logging import progress_logger, result_logger


@pytest.mark.asyncio
async def test_reward_positional_options_keep_their_order_and_cleanup(tmp_path, monkeypatch):
    work = tmp_path / "workspace"
    work.mkdir()
    monkeypatch.setattr(pipeline, "make_workspace", lambda artifacts: work)
    calls = []

    async def score(task, root, config, **options):
        calls.append((task, root, config, options))
        return {"total": {"score": 87}}

    monkeypatch.setattr(pipeline, "score_workspace", score)
    def log(message):
        pass
    previous = {"total": {"score": 60}}
    result = await pipeline.reward("task", [], {}, "reply", "note", "signal", "rubric.json",
                                   previous, log, ("warning",))
    assert result == {"total": {"score": 87}}
    assert calls == [("task", work, {}, {
        "txt_response": "reply", "log": log, "warnings": ("warning",), "rubric_path": "rubric.json",
        "user_note": "note", "process_signal": "signal", "previous_result": previous,
    })]
    assert not work.exists()


@pytest.mark.asyncio
async def test_text_reward_keyword_options_and_cleanup_on_failure(tmp_path, monkeypatch):
    work = tmp_path / "workspace"
    work.mkdir()
    monkeypatch.setattr(pipeline, "make_workspace", lambda **kwargs: work)

    async def fail(task, root, config, **options):
        assert task == "task" and Path(root) == work
        assert options["txt_response"] == "reply"
        assert options["criteria"] == [{"id": "answer"}]
        raise RuntimeError("judge failed")

    monkeypatch.setattr(pipeline, "score_workspace", fail)
    with pytest.raises(RuntimeError, match="judge failed"):
        await pipeline.text_reward("task", {}, {}, response="reply", criteria=[{"id": "answer"}])
    assert not work.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda: pipeline.reward("task", [], {}, unknown=True),
    lambda: pipeline.reward("task", [], {}, "reply", txt_response="duplicate"),
    lambda: pipeline.text_reward("task", {}, {}, response_typo="reply"),
    lambda: pipeline.write_criteria("task", {}, producer_typo=None),
    lambda: pipeline.optional(None, "prompt", "content"),
    lambda: pipeline.evaluate_terminal({}, {}, None),
    lambda: pipeline.feedback_claims("task", "note", "signal"),
    lambda: pipeline.overall_summary("task", [], 80),
    lambda: pipeline.score_workspace("task", ".", {}, criteria_typo=[]),
    lambda: evidence_agent.gather([], {}, {}, lambda message: None, seconds_typo=1),
])
async def test_grouped_arguments_still_reject_invalid_calls(call):
    with pytest.raises(TypeError):
        await call()


@pytest.mark.asyncio
async def test_scoped_progress_restores_nested_and_cancelled_contexts():
    original = progress.current()
    outer, inner = object(), object()
    with progress.scoped(outer):
        with pytest.raises(ValueError, match="failed"):
            with progress.scoped(inner):
                assert progress.current() is inner
                raise ValueError("failed")
        assert progress.current() is outer
        entered = asyncio.Event()
        restored = []

        async def child():
            try:
                with progress.scoped(inner):
                    entered.set()
                    await asyncio.Future()
            finally:
                restored.append(progress.current())

        task = asyncio.create_task(child())
        await entered.wait()
        assert progress.current() is outer
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert restored == [outer]
    assert progress.current() is original


def test_cli_progress_uses_stderr_without_polluting_result_stdout(capsys):
    first = progress_logger()
    second = progress_logger()
    first.info("%s candidates", 2)
    second.info("score %s", 87)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "2 candidates\nscore 87\n"


def test_cli_result_logger_keeps_stdout_plain(capsys):
    result_logger().info('{"total": {"score": 87}}')
    output = capsys.readouterr()
    assert output.out == '{"total": {"score": 87}}\n'
    assert output.err == ""
