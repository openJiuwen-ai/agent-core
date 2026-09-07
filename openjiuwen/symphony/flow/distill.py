"""确定性结构蒸馏：从执行证据提取 skill pack 与质量统计。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.symphony.flow.models import (
    CAN_FEED,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_SUCCESS,
    RECIPE_GRADE_CANDIDATE,
    RECIPE_GRADE_VERIFIED,
    RECIPE_STATUS_ACTIVE,
    RECIPE_STATUS_DRAFT,
    SKILL_PACK_TYPE,
    RecipeEvidence,
    sha256_short,
    utc_now_iso,
)

EDGE_DIRECTION = "directed"
LOOP_GUARDS_EMPTY: list[dict[str, Any]] = []


def normalize_outcome(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"success", "succeeded", "ok", "done"}:
        return OUTCOME_SUCCESS
    if text in {"failed", "failure", "error"}:
        return OUTCOME_FAILED
    if text in {"partial", "partially"}:
        return OUTCOME_PARTIAL
    return OUTCOME_FAILED


def _capability_id(node_id: Any) -> str:
    return str(node_id or "").removeprefix("skill:").removeprefix("capability:")


def normalize_execution_graph(
    payload: dict[str, Any],
) -> RecipeEvidence | None:
    """将 4.3.1 约定的执行图外层对象规范化为 RecipeEvidence。

    graph 结构：nodes（id → label/metadata）、edges（source/target/relation/
    metadata.success）。缺 source/target/success 的边被丢弃；失败原因与
    证据引用保留在边 metadata 中以便追溯，但不进入 skill pack。
    """

    trace_id = str(payload.get("trace_id") or "").strip()
    outcome = normalize_outcome(payload.get("outcome"))
    graph = payload.get("graph")
    if not trace_id or not isinstance(graph, dict):
        return None

    nodes: dict[str, Any] = {}
    for raw_id, raw_node in (graph.get("nodes") or {}).items():
        node_id = _capability_id(raw_id)
        if not node_id or not isinstance(raw_node, dict):
            continue
        metadata = raw_node.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        capability_type = str(metadata.get("capability_type") or raw_node.get("label") or "skill").strip() or "skill"
        nodes[node_id] = {
            "label": str(raw_node.get("label") or ""),
            "metadata": {
                "capability_type": capability_type,
                "version": str(metadata.get("version") or ""),
                "content_hash": str(metadata.get("content_hash") or ""),
            },
        }

    edges: list[dict[str, Any]] = []
    for raw_edge in graph.get("edges") or []:
        if not isinstance(raw_edge, dict):
            continue
        source = _capability_id(raw_edge.get("source"))
        target = _capability_id(raw_edge.get("target"))
        relation = str(raw_edge.get("relation") or CAN_FEED).strip() or CAN_FEED
        metadata = raw_edge.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if "success" not in metadata:
            continue
        success = bool(metadata.get("success"))
        edge: dict[str, Any] = {
            "source": source,
            "target": target,
            "relation": relation,
            "metadata": {"success": success},
        }
        reason = str(metadata.get("reason") or "").strip()
        if reason:
            edge["metadata"]["reason"] = reason
        refs = metadata.get("evidence_refs")
        if isinstance(refs, list) and refs:
            edge["metadata"]["evidence_refs"] = [str(ref) for ref in refs if str(ref).strip()]
        if not source or not target:
            continue
        if source in nodes and target in nodes:
            edges.append(edge)

    normalized_graph = {
        "id": str(graph.get("id") or ""),
        "type": str(graph.get("type") or "execution_graph"),
        "label": str(graph.get("label") or ""),
        "directed": bool(graph.get("directed", True)),
        "nodes": nodes,
        "edges": edges,
    }
    return RecipeEvidence(
        trace_id=trace_id,
        query=str(payload.get("query") or "").strip(),
        outcome=outcome,
        graph=normalized_graph,
    )


@dataclass
class EdgeStats:
    support: int = 0
    success: int = 0

    @property
    def success_rate(self) -> float:
        return self.success / self.support if self.support else 0.0


@dataclass
class DistillResult:
    recipe_id: str
    skill_pack: dict[str, Any]
    quality: dict[str, Any]
    status: str
    grade: str
    member_ids: list[str] = field(default_factory=list)
    group_traces: list[str] = field(default_factory=list)


@dataclass
class StructureGroup:
    """按"合格成功边结构"聚类的一组证据。

    键 = 轨迹【成功边】∩ 全局合格边。失败边不参与分组键（它不是任务
    实际走通的路径），但参与全局边质量统计。组内每条轨迹都完整走过
    pack 的全部边，因此组合级统计（execution_count / success_count /
    pack_success_rate）的归因天然严格。
    """

    signature: str
    edges: tuple[tuple[str, str, str], ...]
    records: list[RecipeEvidence] = field(default_factory=list)


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(edge.get("source") or ""),
        str(edge.get("target") or ""),
        str(edge.get("relation") or CAN_FEED),
    )


def structure_signature(edges: tuple[tuple[str, str, str], ...]) -> str:
    """结构签名：边结构（排序后哈希）——同节点集合不同结构必然分签名。"""

    serialized = "|".join(f"{source}>{target}:{relation}" for source, target, relation in sorted(edges))
    return sha256_short(serialized, length=12)


def qualified_edges(
    records: list[RecipeEvidence],
    *,
    min_edge_support: int,
    min_edge_success_rate: float,
) -> tuple[list[tuple[str, str, str]], dict[tuple[str, str, str], EdgeStats]]:
    """全局边聚合（含失败观测）→ 合格过滤 → 环拆除。

    边质量是全局知识：一条协作关系的 support / success_rate 由全部证据
    决定（失败边正是拉低坏边成功率的证据源），不按组割裂。
    """

    stats = aggregate_edges(records)
    qualified = [
        edge
        for edge, stat in stats.items()
        if stat.support >= min_edge_support and stat.success_rate >= min_edge_success_rate
    ]
    return break_cycles(qualified, stats), stats


def group_by_structure(
    records: list[RecipeEvidence],
    qualified: list[tuple[str, str, str]],
) -> dict[str, StructureGroup]:
    """按合格成功边结构分组；没有任何合格成功边的轨迹不进任何组。"""

    qualified_set = set(qualified)
    groups: dict[str, StructureGroup] = {}
    for record in records:
        success_edges = {
            _edge_key(edge)
            for edge in record.graph.get("edges") or []
            if bool((edge.get("metadata") or {}).get("success"))
        }
        key_edges = tuple(sorted(success_edges & qualified_set))
        if not key_edges:
            continue
        signature = structure_signature(key_edges)
        group = groups.setdefault(
            signature,
            StructureGroup(signature=signature, edges=key_edges),
        )
        group.records.append(record)
    return groups


def aggregate_edges(
    records: list[RecipeEvidence],
) -> dict[tuple[str, str, str], EdgeStats]:
    stats: dict[tuple[str, str, str], EdgeStats] = defaultdict(EdgeStats)
    for record in records:
        for edge in record.graph.get("edges") or []:
            key = _edge_key(edge)
            stats[key].support += 1
            if bool((edge.get("metadata") or {}).get("success")):
                stats[key].success += 1
    return dict(stats)


def break_cycles(
    edges: list[tuple[str, str, str]],
    stats: dict[tuple[str, str, str], EdgeStats],
) -> list[tuple[str, str, str]]:
    """按 support 升序移除环上的弱边直到无环（确定性环拆除）。

    仅删除真正位于有向环上的边：对候选边 source→target，若删除后
    target 仍可达 source，则该边在环上（删除后仍构成环），需要移除。
    环外的树状边不受影响。support 相同时按边元组排序保证确定性。
    """

    def has_cycle(edge_list: list[tuple[str, str, str]]) -> bool:
        graph: dict[str, list[str]] = defaultdict(list)
        for source, target, _relation in edge_list:
            graph[source].append(target)
        visited: set[str] = set()
        stack: set[str] = set()

        def visit(node: str) -> bool:
            if node in stack:
                return True
            if node in visited:
                return False
            visited.add(node)
            stack.add(node)
            for nxt in graph.get(node, []):
                if visit(nxt):
                    return True
            stack.discard(node)
            return False

        return any(visit(node) for node in list(graph))

    def on_cycle(edge: tuple[str, str, str], edge_list: list[tuple[str, str, str]]) -> bool:
        """edge 是否位于有向环上：删除后 target 仍可达 source。"""

        source, target, _relation = edge
        graph: dict[str, list[str]] = defaultdict(list)
        for src, dst, _rel in edge_list:
            if (src, dst, _rel) != edge:
                graph[src].append(dst)
        # target 可达 source ⟺ 存在路径 source→target→...→source（自环已覆盖）
        reachable: set[str] = set()
        pending = [target]
        while pending:
            node = pending.pop()
            if node in reachable:
                continue
            reachable.add(node)
            pending.extend(graph.get(node, []))
        return source in reachable

    kept = list(edges)
    while has_cycle(kept):
        # 候选顺序：support 升序、同 support 按边元组排序（确定性）
        candidates = sorted(
            (edge for edge in kept if on_cycle(edge, kept)),
            key=lambda item: (stats[item].support, item),
        )
        if not candidates:
            break
        kept.remove(candidates[0])
    return kept


def _build_skill_pack(
    member_ids: list[str],
    node_versions: dict[str, dict[str, str]],
    edges: list[tuple[str, str, str]],
    stats: dict[tuple[str, str, str], EdgeStats],
) -> dict[str, Any]:
    pack_edges: list[dict[str, Any]] = []
    for source, target, relation in sorted(edges):
        stat = stats[(source, target, relation)]
        pack_edges.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "metadata": {
                    "support": stat.support,
                    "success_rate": round(stat.success_rate, 4),
                },
            }
        )
    return {
        "type": SKILL_PACK_TYPE,
        "direction": EDGE_DIRECTION,
        "nodes": {
            member_id: {
                "label": "capability",
                "metadata": node_versions.get(member_id, {}),
            }
            for member_id in sorted(member_ids)
        },
        "edges": pack_edges,
        "loop_guards": list(LOOP_GUARDS_EMPTY),
    }


def topological_order(skill_pack: dict[str, Any]) -> list[str]:
    """Kahn 拓扑序：同级按 id 排序，保证确定性。"""

    nodes = skill_pack.get("nodes") or {}
    indegree: dict[str, int] = {node: 0 for node in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in skill_pack.get("edges") or []:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source in indegree and target in indegree:
            adjacency[source].append(target)
            indegree[target] += 1

    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for nxt in sorted(adjacency.get(node, [])):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        ready.sort()
    return order


def compute_quality(
    records: list[RecipeEvidence],
    qualified: list[tuple[str, str, str]],
) -> dict[str, Any]:
    """组合级统计；组内每条轨迹都完整走过 pack 全部边，归因严格。"""

    execution_count = len(records)
    success_count = sum(1 for record in records if record.outcome == OUTCOME_SUCCESS)
    return {
        "execution_count": execution_count,
        "success_count": success_count,
        "failure_count": execution_count - success_count,
        "pack_success_rate": round(success_count / execution_count if execution_count else 0.0, 4),
        "qualified_edge_count": len(qualified),
        "last_success_at": max(
            (record.ingested_at for record in records if record.ingested_at),
            default="",
        ),
    }


def resolve_status_grade(
    quality: dict[str, Any],
    *,
    min_successes_candidate: int,
    min_successes_verified: int,
    min_pack_success_rate_verified: float,
) -> tuple[str, str]:
    success_count = int(quality.get("success_count") or 0)
    pack_success_rate = float(quality.get("pack_success_rate") or 0.0)
    if success_count >= min_successes_verified and pack_success_rate >= min_pack_success_rate_verified:
        return RECIPE_STATUS_ACTIVE, RECIPE_GRADE_VERIFIED
    if success_count >= min_successes_candidate:
        return RECIPE_STATUS_ACTIVE, RECIPE_GRADE_CANDIDATE
    return RECIPE_STATUS_DRAFT, RECIPE_GRADE_CANDIDATE


def distill_group(
    group: StructureGroup,
    stats: dict[tuple[str, str, str], EdgeStats],
    *,
    min_successes_candidate: int,
    min_successes_verified: int,
    min_pack_success_rate_verified: float,
) -> DistillResult:
    """蒸馏一个结构分组：pack = 组结构本身，质量用全局边统计。"""

    records = group.records
    pack_edges = list(group.edges)

    node_versions: dict[str, dict[str, str]] = {}
    for record in records:
        for node_id, node in (record.graph.get("nodes") or {}).items():
            current = node_versions.setdefault(node_id, {})
            metadata = node.get("metadata") or {}
            for key in ("capability_type", "version", "content_hash"):
                value = str(metadata.get(key) or "")
                if value and not current.get(key):
                    current[key] = value

    member_ids = sorted({node for edge in pack_edges for node in (edge[0], edge[1])})
    skill_pack = _build_skill_pack(member_ids, node_versions, pack_edges, stats)
    quality = compute_quality(records, pack_edges)
    status, grade = resolve_status_grade(
        quality,
        min_successes_candidate=min_successes_candidate,
        min_successes_verified=min_successes_verified,
        min_pack_success_rate_verified=min_pack_success_rate_verified,
    )
    return DistillResult(
        recipe_id=f"recipe_{group.signature}",
        skill_pack=skill_pack,
        quality=quality,
        status=status,
        grade=grade,
        member_ids=member_ids,
        group_traces=[record.trace_id for record in records],
    )


def recipe_provenance(
    result: DistillResult,
    records: list[RecipeEvidence],
) -> dict[str, Any]:
    return {
        "source": "trajectory_distillation",
        "structure_signature": result.recipe_id.removeprefix("recipe_"),
        "evidence_trace_ids": result.group_traces,
        "evidence_count": len(records),
        "sample_queries": [record.query for record in records if record.query][:5],
        "distilled_at": utc_now_iso(),
    }
