"""Parallel selection does not publish a batch of empty UI nodes."""

from types import SimpleNamespace

from openjiuwen.rsi.artifact_rsi.program_opt import events
from openjiuwen.rsi.artifact_rsi.program_opt.candidates import CandidateStore
from openjiuwen.rsi.artifact_rsi.program_opt.engine import RunSpec
from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import _Reporter, _Usage
from openjiuwen.rsi.artifact_rsi.program_opt.state import ProgramRunState, read_tree_file
from openjiuwen.rsi.artifact_rsi.program_opt.tree import PuctTree


def test_parallel_selection_stays_hidden_until_expansion(tmp_path):
    state = ProgramRunState(task_id="timing", run_dir=tmp_path, total_iterations=3)
    public = []
    def emit(event):
        public.extend(state.absorb(event))
    emit(events.seeded(0, 0.1))
    public.clear()
    tree = PuctTree()
    tree.nodes.append(SimpleNamespace(parent_index=None, score=0.1))
    spec = RunSpec(search_id="timing", algorithm="puct", expansions=3,
                   scorecard_hash="sha256:x", scorecard={"criteria": []},
                   statement="", baseline_code="x = 1", script="s")
    reporter = _Reporter(spec, tree, object(), CandidateStore(tmp_path, flat=True), _Usage(), emit)
    for iteration in (1, 2, 3):
        reporter.on_event("selected", {"iteration": iteration, "parent_index": 0,
                                      "ancestors": [{"nodeIndex": 0, "numVisits": iteration}]})
        reporter.on_event("stage", {"iteration": iteration, "id": "generate", "name": "正在生成程序"})
    assert public == []
    assert len(read_tree_file("timing").nodes) == 1
    # Completion order is independent of selection order.
    for index, iteration in enumerate((2, 1, 3), 1):
        node = SimpleNamespace(index=index, parent_index=0, promise=None,
                               program=SimpleNamespace(valid=False, error="generation failed"))
        reporter.on_event("node", {"node": node, "metrics": {},
                                  "ops": {"iteration": iteration, "code": "x = 1"}})
        assert state.nodes[index].node_id == f"artifact:timing:attempt:{iteration}"
        assert state.nodes[index].parent_id == state.nodes[0].node_id
        assert len(read_tree_file("timing").nodes) == index + 1
    assert state.iteration == 0
