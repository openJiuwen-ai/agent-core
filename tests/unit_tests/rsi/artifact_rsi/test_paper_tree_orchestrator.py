import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import (
    TerminalReport,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider import orchestrator as module
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.projection import (
    project_engine_report,
    project_engine_state,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.schemas import (
    PaperNodeExtra,
    RsiTreeNode,
)
from openjiuwen.rsi.usage import record_model_usage


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        (
            TerminalReport(
                status="failed",
                run_id="run-source",
                failure_reason="download source failed: raw error",
            ),
            "资料获取质量不佳，已剪枝。",
        ),
        (
            TerminalReport(
                status="failed",
                run_id="run-experiment",
                failure_reason="experiment execution did not produce results",
            ),
            "实验验证效果不佳，已剪枝。",
        ),
    ],
)
def test_pruned_reason_is_short_and_user_facing(terminal, expected):
    reason = module._friendly_pruned_reason(terminal=terminal)  # noqa: SLF001

    assert reason == expected
    assert "raw error" not in reason


def test_scoring_pruned_reason_does_not_expose_exception():
    reason = module._friendly_pruned_reason(  # noqa: SLF001
        failure_class="scoring_error",
        phase="scoring",
        terminal=TerminalReport(
            status="complete",
            run_id="run-score",
            failure_reason="provider error with traceback",
        ),
    )

    assert reason == "评估结果未达到预期，已剪枝。"
    assert "provider error" not in reason


@pytest.mark.asyncio
async def test_uploaded_latex_baseline_score_is_persisted_and_projected(tmp_path, monkeypatch):
    source = tmp_path / "uploaded-paper"
    source.mkdir()
    (source / "main.tex").write_text(
        r"\documentclass{article}\begin{document}baseline\end{document}",
        encoding="utf-8",
    )
    calls = []

    async def fake_score_paper(*, tex_path, output_dir, config, model):
        calls.append((tex_path, output_dir, config, model))
        return module.PaperScore(overall=7.25, breakdown={"clarity": 7.0})

    monkeypatch.setattr(module, "score_paper", fake_score_paper)
    orchestrator = module.PaperTreeOrchestrator(
        task_id="baseline-score",
        run_dir=str(tmp_path / "task"),
        max_iterations=0,
        optimization_instruction="improve the paper",
        artifact_path=str(source),
    )

    await orchestrator.start()
    await orchestrator._task  # noqa: SLF001 - await the provider loop

    state = orchestrator.storage.load_task_state()
    root = orchestrator.storage.load_tree()[0]
    assert state is not None
    assert state.score == 7.25
    assert state.baseline == 7.25
    assert root.score == 7.25
    assert root.paper_extra is not None
    assert root.paper_extra.score_overall == 7.25
    assert calls and calls[0][0].endswith("input/paper/uploaded-paper/main.tex")

    projected_state = project_engine_state(state)
    projected_report = project_engine_report(state, artifact_index=[])
    assert projected_state.score == 7.25
    assert projected_state.baseline == 7.25
    assert projected_report.best_score == 7.25
    assert projected_report.baseline == 7.25


@pytest.mark.asyncio
async def test_paper_usage_is_persisted_and_emitted_for_baseline_scoring(tmp_path, monkeypatch):
    source = tmp_path / "uploaded-paper"
    source.mkdir()
    (source / "main.tex").write_text(
        r"\documentclass{article}\begin{document}baseline\end{document}",
        encoding="utf-8",
    )
    events = []

    async def fake_score_paper(*, tex_path, output_dir, config, model):
        del tex_path, output_dir, config, model
        await record_model_usage(
            model="paper-scorer",
            call_id="baseline-score-call",
            usage={"input_tokens": 12, "output_tokens": 7, "cache_read_tokens": 3},
        )
        return module.PaperScore(overall=8.0, breakdown={"clarity": 8.0})

    async def on_event(event):
        events.append(event)

    monkeypatch.setattr(module, "score_paper", fake_score_paper)
    run_dir = tmp_path / "task"
    orchestrator = module.PaperTreeOrchestrator(
        task_id="usage-baseline",
        run_dir=str(run_dir),
        max_iterations=0,
        optimization_instruction="improve the paper",
        artifact_path=str(source),
        on_event=on_event,
    )

    await orchestrator.start()
    await orchestrator._task  # noqa: SLF001 - await the provider loop

    state = orchestrator.storage.load_task_state()
    assert state is not None
    assert state.usage is not None
    assert state.usage.tokens.input == 12
    assert state.usage.tokens.output == 7
    assert state.usage.tokens.cache_hit == 3
    assert state.usage.call_count == 1
    assert project_engine_state(state).usage == state.usage
    assert (run_dir / "model_calls.jsonl").is_file()
    progress = [event for event in events if isinstance(event, module.EventProgress)]
    assert progress and progress[-1].usage == state.usage


@pytest.mark.asyncio
async def test_failed_manager_run_becomes_pruned_node_without_raw_error(tmp_path):
    orchestrator = module.PaperTreeOrchestrator(
        task_id="pruned-node",
        run_dir=str(tmp_path),
        max_iterations=1,
        optimization_instruction="improve the paper",
        artifact_path=None,
    )
    parent = RsiTreeNode(
        node_id="root",
        iteration=0,
        parent_id=None,
        type="root",
        adopted=True,
    )
    terminal = TerminalReport(
        status="failed",
        run_id="run-1",
        failure_reason="Task loop round timed out after 600 seconds; traceback",
        summary="manager failed",
    )

    node = await orchestrator._build_node(  # noqa: SLF001
        node_id="node-1",
        round_index=1,
        attempt=1,
        parent=parent,
        node_run_id="run-1",
        terminal=terminal,
    )

    assert node.type == "pruned"
    assert node.adopted is False
    assert node.reason == "当前方案效果未达到要求，已剪枝。"
    assert node.summary is None
    assert node.failure_class == "pipeline_failed"
    assert node.paper_extra is not None
    assert node.paper_extra.logical_kind == "pruned"
    assert node.paper_extra.outcome == "failed"
    assert "timeout" not in (node.reason or "").lower()
    assert "traceback" not in (node.reason or "").lower()


def _adopted_node(*, node_id: str, iteration: int, parent_id: str | None) -> RsiTreeNode:
    return RsiTreeNode(
        node_id=node_id,
        iteration=iteration,
        parent_id=parent_id,
        type="reporting",
        adopted=True,
        summary="generated paper",
        extra={
            "paper": PaperNodeExtra(
                logical_kind="adopted",
                round_index=iteration,
                attempt=1,
                input_node_id=parent_id,
                retry_of_node_id=parent_id,
                outcome="success",
                node_run_id=f"run-{iteration}",
                score_overall=1.0,
            ).model_dump(mode="json")
        },
    )


@pytest.mark.asyncio
async def test_pruned_node_does_not_stop_outer_search_before_later_success(tmp_path, monkeypatch):
    orchestrator = module.PaperTreeOrchestrator(
        task_id="pruned-then-success",
        run_dir=str(tmp_path),
        max_iterations=2,
        optimization_instruction="improve the paper",
        artifact_path=None,
    )
    manager_calls = []

    async def fake_run_manager(seed):
        manager_calls.append(seed.run_id)
        return TerminalReport(
            status="failed" if len(manager_calls) == 1 else "complete",
            run_id=seed.run_id,
            failure_reason="experiment execution did not produce results" if len(manager_calls) == 1 else "",
        )

    async def fake_build_node(**kwargs):
        if kwargs["round_index"] == 1:
            return orchestrator._pruned_node(  # noqa: SLF001
                node_id=kwargs["node_id"],
                round_index=kwargs["round_index"],
                attempt=kwargs["attempt"],
                parent_id=kwargs["parent"].node_id,
                node_run_id=kwargs["node_run_id"],
                reason="实验验证效果不佳，已剪枝。",
                failure_class="pipeline_failed",
            )
        return _adopted_node(
            node_id=kwargs["node_id"],
            iteration=kwargs["round_index"],
            parent_id=kwargs["parent"].node_id,
        )

    monkeypatch.setattr(orchestrator, "_run_manager", fake_run_manager)
    monkeypatch.setattr(orchestrator, "_build_node", fake_build_node)

    await orchestrator.start()
    await orchestrator._task  # noqa: SLF001 - await the provider loop

    state = orchestrator.storage.load_task_state()
    nodes = orchestrator.storage.load_tree()
    assert state is not None
    assert manager_calls == ["pruned-then-success-r1", "pruned-then-success-r2"]
    assert [node.type for node in nodes] == ["root", "pruned", "reporting"]
    assert nodes[1].reason == "实验验证效果不佳，已剪枝。"
    assert state.status == "completed"
    assert state.best_node_id == nodes[2].node_id
    assert state.frontier_node_id == nodes[2].node_id


@pytest.mark.asyncio
async def test_all_pruned_nodes_end_task_as_failed_without_false_success(tmp_path, monkeypatch):
    orchestrator = module.PaperTreeOrchestrator(
        task_id="all-pruned",
        run_dir=str(tmp_path),
        max_iterations=2,
        optimization_instruction="improve the paper",
        artifact_path=None,
    )

    async def fake_run_manager(seed):
        return TerminalReport(
            status="failed",
            run_id=seed.run_id,
            failure_reason="manager error details",
        )

    monkeypatch.setattr(orchestrator, "_run_manager", fake_run_manager)

    await orchestrator.start()
    await orchestrator._task  # noqa: SLF001 - await the provider loop

    state = orchestrator.storage.load_task_state()
    nodes = orchestrator.storage.load_tree()
    assert state is not None
    assert state.status == "failed"
    assert state.best_node_id is None
    assert state.error_message == "未生成可用的论文结果。"
    assert [node.type for node in nodes] == ["root", "pruned", "pruned"]
    assert all(node.reason and "error" not in node.reason.lower() for node in nodes[1:])
