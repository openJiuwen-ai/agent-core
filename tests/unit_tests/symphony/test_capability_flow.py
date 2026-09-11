"""Capability flow（经验沉淀 + 能力包）端到端测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from openjiuwen.symphony.flow import (
    RECIPE_GRADE_CANDIDATE,
    RECIPE_GRADE_VERIFIED,
    RECIPE_STATUS_ACTIVE,
    RECIPE_STATUS_DRAFT,
    TARGET_KIND_PLUGIN,
    VERDICT_APPROVED,
    VERDICT_REJECTED,
    CapabilityPackager,
    SymphonyFlowEngine,
)
from openjiuwen.symphony.flow.codegen import (
    generate_swarmflow_script,
    validate_generated_script,
    validate_meta_name,
)
from openjiuwen.symphony.flow.distill import (
    EdgeStats,
    break_cycles,
    normalize_execution_graph,
    topological_order,
)
from openjiuwen.symphony.orchestration import SymphonyFlowConfig


def _execution_graph(
    trace_id: str,
    *,
    query: str = "调研多智能体系统进展并写综述报告",
    outcome: str = "success",
    failed_edge: bool = False,
) -> dict:
    edges = [
        {
            "source": "skill:web-search",
            "target": "skill:summarize-paper",
            "relation": "can_feed",
            "metadata": {"success": True},
        },
        {
            "source": "skill:summarize-paper",
            "target": "skill:write-report",
            "relation": "can_feed",
            "metadata": {"success": not failed_edge},
        },
    ]
    return {
        "trace_id": trace_id,
        "query": query,
        "outcome": outcome,
        "graph": {
            "id": f"graph_{trace_id}",
            "type": "execution_graph",
            "directed": True,
            "nodes": {
                "skill:web-search": {
                    "label": "skill",
                    "metadata": {"version": "1.0.0"},
                },
                "skill:summarize-paper": {
                    "label": "skill",
                    "metadata": {"version": "1.0.0"},
                },
                "skill:write-report": {
                    "label": "skill",
                    "metadata": {"version": "1.0.0"},
                },
            },
            "edges": edges,
        },
    }


def _engine(tmp_path: Path) -> SymphonyFlowEngine:
    config = SymphonyFlowConfig(
        min_edge_support=2,
        min_edge_success_rate=0.8,
        min_successes_candidate=3,
        min_successes_verified=5,
        min_pack_success_rate_verified=0.8,
    )
    return SymphonyFlowEngine(tmp_path / "flow", config=config)


def _verified_recipe_id(engine: SymphonyFlowEngine, report) -> str:
    """从蒸馏报告中取 active+verified 的 recipe（绕行子结构会另成组）。"""

    for recipe_id in report.recipes_saved:
        recipe = engine.get_recipe(recipe_id)
        if recipe is not None and recipe.status == RECIPE_STATUS_ACTIVE and recipe.grade == RECIPE_GRADE_VERIFIED:
            return recipe_id
    raise AssertionError("no verified recipe distilled")


def _feed_verified_engine(tmp_path: Path) -> SymphonyFlowEngine:
    engine = _engine(tmp_path)
    for index in range(6):
        engine.ingest(
            _execution_graph(
                f"trace-{index}",
                query=f"调研任务 {index}",
                failed_edge=index == 0,
            )
        )
    return engine


def test_ingest_and_distill_end_to_end(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)

    # trace_id 幂等
    assert engine.ingest(_execution_graph("trace-0")) is False

    report = asyncio.run(engine.distill())
    assert report.evidence_total == 6
    # 两条结构组：完整链（5 条轨迹）+ 绕行子结构（trace-0 仅走通前半段）
    assert len(report.recipes_saved) == 2
    recipe_id = _verified_recipe_id(engine, report)

    recipe = engine.get_recipe(recipe_id)
    assert recipe is not None
    assert recipe.status == RECIPE_STATUS_ACTIVE
    assert recipe.grade == RECIPE_GRADE_VERIFIED
    assert set(recipe.capability_ids) == {
        "web-search",
        "summarize-paper",
        "write-report",
    }
    # 归因严格：trace-0（靠前半段成功、write 边失败）不计入完整链统计
    assert recipe.quality["execution_count"] == 5
    assert recipe.quality["success_count"] == 5
    assert recipe.quality["pack_success_rate"] == 1.0
    assert set(recipe.provenance["evidence_trace_ids"]) == {f"trace-{index}" for index in range(1, 6)}

    # 绕行子结构：单边 {web-search → summarize-paper}，证据不足以 active
    bypass_ids = [recipe_id_ for recipe_id_ in report.recipes_saved if recipe_id_ != recipe_id]
    bypass = engine.get_recipe(bypass_ids[0])
    assert bypass is not None
    assert set(bypass.capability_ids) == {"web-search", "summarize-paper"}
    assert bypass.quality["execution_count"] == 1
    assert bypass.status == RECIPE_STATUS_DRAFT

    # 内容未变化时 distill 不产生新版本
    report_again = asyncio.run(engine.distill())
    assert report_again.recipes_saved == []
    assert sorted(report_again.recipes_unchanged) == sorted(report.recipes_saved)
    assert engine.get_recipe(recipe_id).version == 1


def test_prepare_install_approved(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)

    preparation = engine.prepare_install(recipe_id)
    assert preparation.verdict == VERDICT_APPROVED
    assert preparation.artifact_dir is not None

    artifact_dir = Path(preparation.artifact_dir)
    assert (artifact_dir / "SKILL.md").is_file()
    assert (artifact_dir / "dependencies.yaml").is_file()
    assert (artifact_dir / "swarmflow" / "run.py").is_file()

    package = preparation.package
    assert package is not None
    assert package["materials"]["swarmflow_script"]
    problems = validate_generated_script(package["materials"]["swarmflow_script"])
    assert problems == []
    assert validate_meta_name(package["materials"]["meta_name"])

    skill_md = (artifact_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## 任务描述" in skill_md
    assert "## 执行过程" in skill_md

    # 完整性校验
    assert CapabilityPackager.verify_package_integrity(package)


def test_prepare_install_rejections(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)

    # recipe 不存在
    missing = engine.prepare_install("recipe_missing")
    assert missing.verdict == VERDICT_REJECTED
    assert missing.package is None

    # plugin 目标暂不支持
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)
    plugin = engine.prepare_install(recipe_id, target_kind=TARGET_KIND_PLUGIN)
    assert plugin.verdict == VERDICT_REJECTED

    # 证据不足：结构无法达到 active/verified，install 准备必须拒绝
    lone = _engine(tmp_path / "lone")
    lone.ingest(_execution_graph("trace-lone"))
    report = asyncio.run(lone.distill())
    assert report.recipes_saved == []  # support < 2 → 无合格结构


# 4.3.1 约定的执行图外层对象样本：含失败边、分支边与证据引用
_DOCUMENTED_EXECUTION_GRAPH = {
    "trace_id": "trace_20260820_001",
    "query": "整理调研数据并生成分析报告",
    "outcome": "success",
    "graph": {
        "id": "execution_graph_20260820_001",
        "type": "execution_graph",
        "label": "capability execution graph",
        "directed": True,
        "nodes": {
            "skill1": {
                "label": "skill",
                "metadata": {"version": "1.0.0", "content_hash": "sha256:skill1-content"},
            },
            "skill2": {
                "label": "skill",
                "metadata": {"version": "1.0.0", "content_hash": "sha256:skill2-content"},
            },
            "skill3": {
                "label": "skill",
                "metadata": {"version": "1.0.0", "content_hash": "sha256:skill3-content"},
            },
            "skill5": {
                "label": "skill",
                "metadata": {"version": "1.0.0", "content_hash": "sha256:skill5-content"},
            },
        },
        "edges": [
            {
                "source": "skill1",
                "target": "skill2",
                "relation": "can_feed",
                "metadata": {
                    "success": True,
                    "evidence_refs": ["trace_20260820_001#events=6-14"],
                },
            },
            {
                "source": "skill2",
                "target": "skill3",
                "relation": "can_feed",
                "metadata": {
                    "success": False,
                    "reason": "skill3 未能继续处理 skill2 产生的中间结果",
                    "evidence_refs": ["trace_20260820_001#events=15-23"],
                },
            },
            {
                "source": "skill2",
                "target": "skill5",
                "relation": "can_feed",
                "metadata": {
                    "success": True,
                    "evidence_refs": ["trace_20260820_001#events=24-36"],
                },
            },
        ],
    },
}


def test_ingest_accepts_documented_execution_graph(tmp_path: Path) -> None:
    """4.3.1 文档格式的执行图可直接接入：失败边剔除成员、分支边保留。"""

    config = SymphonyFlowConfig(
        min_edge_support=1,
        min_edge_success_rate=0.5,
        min_successes_candidate=1,
        min_successes_verified=1,
        min_pack_success_rate_verified=0.5,
    )
    engine = SymphonyFlowEngine(tmp_path / "flow", config=config)

    assert engine.ingest(_DOCUMENTED_EXECUTION_GRAPH) is True
    # 证据层保留失败原因与证据引用（可追溯，但不进 skill pack）
    stored = engine.store.read_evidence()[0]
    reasons = [edge["metadata"].get("reason") for edge in stored.graph["edges"]]
    assert "skill3 未能继续处理 skill2 产生的中间结果" in reasons

    report = asyncio.run(engine.distill())
    assert len(report.recipes_saved) == 1
    recipe = engine.get_recipe(report.recipes_saved[0])
    pack = recipe.combination_structure

    # 失败边（skill2→skill3）不合格：skill3 不进入组合
    assert set(pack["nodes"]) == {"skill1", "skill2", "skill5"}
    # 同一源（skill2）的多条合格出边保留为分支
    edge_keys = {(edge["source"], edge["target"]) for edge in pack["edges"]}
    assert edge_keys == {("skill1", "skill2"), ("skill2", "skill5")}
    # 整体 outcome 与边级归因独立：样本整体成功
    assert recipe.quality["execution_count"] == 1
    assert recipe.quality["success_count"] == 1


def test_verified_requires_pack_success_rate(tmp_path: Path) -> None:
    """组合成功率不足时即便成功次数达标也不给 verified。"""

    config = SymphonyFlowConfig(
        min_edge_support=1,
        min_edge_success_rate=0.5,
        min_successes_candidate=1,
        min_successes_verified=3,
        min_pack_success_rate_verified=0.8,
    )
    engine = SymphonyFlowEngine(tmp_path / "flow", config=config)
    for index in range(8):
        engine.ingest(
            _execution_graph(
                f"trace-{index}",
                query=f"调研任务 {index}",
                outcome="success" if index < 6 else "failed",
            )
        )

    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(report.recipes_saved[0])

    # 6 次成功 ≥ 3，但组合成功率 6/8 = 0.75 < 0.8 → 卡在 candidate
    assert recipe.quality["success_count"] == 6
    assert recipe.quality["pack_success_rate"] == 0.75
    assert recipe.grade == RECIPE_GRADE_CANDIDATE
    assert recipe.status == RECIPE_STATUS_ACTIVE

    # 全部成功时（8/8 = 1.0）恢复 verified
    engine2 = SymphonyFlowEngine(tmp_path / "flow2", config=config)
    for index in range(8):
        engine2.ingest(_execution_graph(f"trace-{index}", query=f"调研任务 {index}"))
    report2 = asyncio.run(engine2.distill())
    recipe2 = engine2.get_recipe(report2.recipes_saved[0])
    assert recipe2.quality["pack_success_rate"] == 1.0
    assert recipe2.grade == RECIPE_GRADE_VERIFIED


def test_generated_script_uses_whitelisted_operators(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)
    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(_verified_recipe_id(engine, report))

    script = generate_swarmflow_script(
        recipe.combination_structure,
        recipe_id=recipe.recipe_id,
        task_description="调研并生成报告",
    )
    assert "from swarmflow import agent" in script
    assert "await agent(" in script
    assert validate_generated_script(script) == []


def _graph_with_edges(
    trace_id: str,
    edges: list[tuple[str, str, bool]],
) -> dict:
    """构造固定节点集合 {s1,s2,s3,s5}、自定义边成败的执行图。"""

    return {
        "trace_id": trace_id,
        "query": f"任务 {trace_id}",
        "outcome": "success",
        "graph": {
            "id": f"graph_{trace_id}",
            "type": "execution_graph",
            "directed": True,
            "nodes": {node: {"label": "skill", "metadata": {"version": "1.0.0"}} for node in ("s1", "s2", "s3", "s5")},
            "edges": [
                {
                    "source": source,
                    "target": target,
                    "relation": "can_feed",
                    "metadata": {"success": success},
                }
                for source, target, success in edges
            ],
        },
    }


def test_same_nodes_different_structures_split_groups(tmp_path: Path) -> None:
    """同节点集合 ≠ 同 pack：按合格成功边结构分组，归因互不污染。

    全部轨迹节点集合均为 {s1,s2,s3,s5}：
    - t1/t2/t3/t6 走 s1→s2→s5（t6 另有失败尝试 s5→s3）
    - t4/t5     走 s1→s2→s3
    期望：两个独立 recipe；t6 的失败边不分裂分组、t4/t5 的成功
    不给 s1→s2→s5 组虚增成功经验（边方向由 source→target 决定）。
    """

    config = SymphonyFlowConfig(
        min_edge_support=2,
        min_edge_success_rate=0.8,
        min_successes_candidate=2,
        min_successes_verified=3,
        min_pack_success_rate_verified=0.8,
    )
    engine = SymphonyFlowEngine(tmp_path / "flow", config=config)
    engine.ingest(_graph_with_edges("t1", [("s1", "s2", True), ("s2", "s5", True)]))
    engine.ingest(_graph_with_edges("t2", [("s1", "s2", True), ("s2", "s5", True)]))
    engine.ingest(_graph_with_edges("t3", [("s1", "s2", True), ("s2", "s5", True)]))
    engine.ingest(_graph_with_edges("t4", [("s1", "s2", True), ("s2", "s3", True)]))
    engine.ingest(_graph_with_edges("t5", [("s1", "s2", True), ("s2", "s3", True)]))
    # 失败尝试（s5→s3）：不影响分组键，也不给任何 pack 计成功
    engine.ingest(
        _graph_with_edges(
            "t6",
            [("s1", "s2", True), ("s2", "s5", True), ("s5", "s3", False)],
        )
    )

    report = asyncio.run(engine.distill())
    assert len(report.recipes_saved) == 2

    by_traces = {}
    for recipe_id in report.recipes_saved:
        recipe = engine.get_recipe(recipe_id)
        by_traces[tuple(sorted(recipe.provenance["evidence_trace_ids"]))] = recipe

    # 组1：s1→s2→s5（t1/t2/t3/t6），execution_count 不含 t4/t5 的成功
    group1 = by_traces[("t1", "t2", "t3", "t6")]
    assert set(group1.capability_ids) == {"s1", "s2", "s5"}
    assert group1.quality["execution_count"] == 4
    assert group1.quality["success_count"] == 4
    assert group1.grade == RECIPE_GRADE_VERIFIED

    # 组2：s1→s2→s3（t4/t5），独立 pack、独立统计
    group2 = by_traces[("t4", "t5")]
    assert set(group2.capability_ids) == {"s1", "s2", "s3"}
    assert group2.quality["execution_count"] == 2

    # 两个 pack 的边结构不同（方向由 source→target 决定）
    edges1 = {(edge["source"], edge["target"]) for edge in group1.combination_structure["edges"]}
    edges2 = {(edge["source"], edge["target"]) for edge in group2.combination_structure["edges"]}
    assert edges1 == {("s1", "s2"), ("s2", "s5")}
    assert edges2 == {("s1", "s2"), ("s2", "s3")}
    assert group1.recipe_id != group2.recipe_id


def test_team_subagent_nodes_keep_capability_type() -> None:
    evidence = normalize_execution_graph(
        {
            "trace_id": "team-trace",
            "outcome": "success",
            "graph": {
                "id": "team-graph",
                "type": "execution_graph",
                "directed": True,
                "nodes": {
                    "leader": {
                        "label": "subagent",
                        "metadata": {"version": "v1", "content_hash": "leader-hash"},
                    },
                    "writer": {
                        "label": "subagent",
                        "metadata": {"version": "v1", "content_hash": "writer-hash"},
                    },
                },
                "edges": [
                    {
                        "source": "leader",
                        "target": "writer",
                        "relation": "can_feed",
                        "metadata": {"success": True},
                    }
                ],
            },
        }
    )

    assert evidence is not None
    assert evidence.graph["nodes"]["leader"]["metadata"]["capability_type"] == "subagent"
    assert evidence.graph["nodes"]["writer"]["metadata"]["capability_type"] == "subagent"


def _stats(support: int) -> EdgeStats:
    return EdgeStats(support=support, success=support)


def test_break_cycles_removes_weakest_edge_on_ring() -> None:
    """纯环输入：拆除一条环边后必须无环，且拓扑序覆盖全部成员。"""

    edges = [("A", "B", "r"), ("B", "C", "r"), ("C", "A", "r")]
    stats = {edge: _stats(5) for edge in edges}

    kept = break_cycles(edges, stats)

    assert len(kept) == 2
    # 同 support 按边元组排序：删除 ("A", "B", "r")
    assert ("A", "B", "r") not in kept
    pack = {
        "nodes": {node: {} for node in ("A", "B", "C")},
        "edges": [{"source": src, "target": dst} for src, dst, _ in kept],
    }
    assert sorted(topological_order(pack)) == ["A", "B", "C"]


def test_break_cycles_keeps_tree_edges_outside_ring() -> None:
    """环外树边不参与拆环：即使 support 最低也不能被误删。"""

    edges = [("A", "B", "r"), ("B", "C", "r"), ("C", "A", "r"), ("D", "E", "r")]
    stats = {
        ("A", "B", "r"): _stats(5),
        ("B", "C", "r"): _stats(5),
        ("C", "A", "r"): _stats(5),
        ("D", "E", "r"): _stats(1),
    }

    kept = break_cycles(edges, stats)

    assert ("D", "E", "r") in kept
    assert len(kept) == 3


def test_break_cycles_removes_self_loop() -> None:
    kept = break_cycles([("A", "A", "r")], {("A", "A", "r"): _stats(5)})

    assert kept == []


def test_break_cycles_handles_multiple_rings_by_support() -> None:
    """8 字双环：每个环各拆一条 support 最低的环边。"""

    edges = [
        ("A", "B", "r"),
        ("B", "C", "r"),
        ("C", "A", "r"),
        ("C", "D", "r"),
        ("D", "E", "r"),
        ("E", "C", "r"),
    ]
    stats = {
        ("A", "B", "r"): _stats(9),
        ("B", "C", "r"): _stats(9),
        ("C", "A", "r"): _stats(2),
        ("C", "D", "r"): _stats(9),
        ("D", "E", "r"): _stats(9),
        ("E", "C", "r"): _stats(1),
    }

    kept = break_cycles(edges, stats)

    # 环1 拆 C→A（support=2），环2 拆 E→C（support=1），公共边 B→C 保留
    assert ("C", "A", "r") not in kept
    assert ("E", "C", "r") not in kept
    assert len(kept) == 4


def test_break_cycles_acyclic_input_unchanged() -> None:
    edges = [("A", "B", "r"), ("B", "C", "r")]

    assert break_cycles(edges, {edge: _stats(5) for edge in edges}) == edges
