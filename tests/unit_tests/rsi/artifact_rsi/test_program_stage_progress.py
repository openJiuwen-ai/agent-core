from openjiuwen.rsi.artifact_rsi.program_opt import events
from openjiuwen.rsi.artifact_rsi.program_opt.state import ProgramRunState, read_tree_file


def test_parallel_candidates_keep_identity_parent_artifacts_and_completed_count(tmp_path):
    run = ProgramRunState(task_id="parallel", run_dir=tmp_path, total_iterations=3)
    def emit(event):
        return list(run.absorb(event))
    emit(events.seeded(0, 0.1))
    first = emit({"type": "candidate_started", "iteration": 1, "parentIndex": 0})[0].node
    second = emit({"type": "candidate_started", "iteration": 2, "parentIndex": 0})[0].node
    stage = emit({"type": "stage", "iteration": 1, "id": "check", "name": "正在试运行程序"})[0]
    assert stage.node_ref == first.node_id
    assert read_tree_file("parallel").iteration == 0
    # The second attempt arrives first and receives engine index 1.
    emit(events.expanded(1, 0, 1, 0.3, True, iteration=2, code_hash="abc"))
    assert run.nodes[1].node_id == second.node_id
    assert next(iter(run.artifacts.values())).node_id == second.node_id
    emit(events.merged(1, True, "better"))
    assert run.iteration == read_tree_file("parallel").iteration == 1
    emit(events.expanded(2, 0, 1, 0.2, True, iteration=1))
    emit(events.merged(2, False, "lower"))
    child = emit({"type": "candidate_started", "iteration": 3, "parentIndex": 1})[0].node
    assert child.parent_id == second.node_id
    assert run.nodes[2].node_id == first.node_id
    assert len(run.nodes) == 4
    assert run.iteration == 2
    restored = ProgramRunState(task_id="parallel", run_dir=tmp_path, total_iterations=3)
    restored.rehydrate()
    assert restored.nodes[1].node_id == second.node_id
    assert restored.nodes[2].node_id == first.node_id
    restored.finish()
    assert all(node.type != "provisional" for node in read_tree_file("parallel").nodes)
    assert read_tree_file("parallel").iteration == 2
