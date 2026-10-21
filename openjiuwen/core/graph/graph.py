# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Hashable,
    Self,
    Sequence,
    Tuple,
    Union,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import graph_logger, LogEventType
from openjiuwen.core.graph.base import (
    ExecutableGraph,
    Graph,
    Router,
)
from openjiuwen.core.graph.executable import (
    Executable,
    Input,
    Output,
)
from openjiuwen.core.graph.pregel import (
    END,
    MAX_RECURSIVE_LIMIT,
    Pregel,
    PregelBuilder,
    PregelConfig,
    START,
)
from openjiuwen.core.graph.pregel.constants import SESSION_ID
from openjiuwen.core.graph.store import GraphStore
from openjiuwen.core.graph.vertex import Vertex
from openjiuwen.core.session import (
    BaseSession,
    Checkpointer,
    InteractiveInput,
)
from openjiuwen.core.session.workflow import Session


@dataclass(slots=True)
class Branch:
    condition: Callable[..., Hashable | Sequence[Hashable]]


class PregelGraph(Graph):
    def __init__(self):
        self.pregel: Pregel | None = None
        self.edges: list[Tuple[str | list[str], str]] = []
        self.waits: set[str] = set()
        self.nodes: dict[str, Vertex] = {}
        self.branches: defaultdict[str, dict[str, Branch]] = defaultdict(dict)
        self.branch_targets: dict[str, set[str]] = {}
        self.checkpointer = None
        self._session = None
        # 结构解析缓存（_parse_structure 产物）：edges/waits/branches/branch_targets
        # 均为构建期数据，任何结构修改方法写入时置脏（置 None），compile 命中即复用。
        self._structure_cache: tuple | None = None

    def start_node(self, node_id: str) -> Self:
        self._validate_node_id(node_id)
        self.add_edge([START], node_id)
        return self

    def _validate_node_id(self, node_id):
        if not node_id:
            raise build_error(StatusCode.PREGEL_GRAPH_NODE_ID_INVALID, node_id=node_id, reason="is None or empty")

    def end_node(self, node_id: str) -> Self:
        self._validate_node_id(node_id)
        vertex = self.nodes.get(node_id)
        if vertex:
            vertex.is_end_node = True
        self.add_edge([node_id], END)
        return self

    def add_node(self, node_id: str, node: Executable, *, wait_for_all: bool = False) -> Self:
        self._validate_node_id(node_id)
        if node is None:
            raise build_error(StatusCode.PREGEL_GRAPH_NODE_INVALID, node_id=node_id, reason="node is None")

        if node_id in self.nodes:
            raise build_error(
                StatusCode.PREGEL_GRAPH_NODE_ID_INVALID, node_id=node_id, reason="already exist, can not add again"
            )
        vertex_node = Vertex(node_id, node)
        self.nodes[node_id] = vertex_node
        if wait_for_all:
            self.waits.add(node_id)
            self._structure_cache = None
        return self

    def get_nodes(self) -> dict[str, Vertex]:
        return {key: vertex for key, vertex in self.nodes.items()}

    def add_edge(self, source_node_id: Union[str, list[str]], target_node_id: str) -> Self:
        if not source_node_id:
            raise build_error(
                StatusCode.PREGEL_GRAPH_EDGE_INVALID,
                source_id=source_node_id,
                target_node_id=target_node_id,
                reason="source_node_id is None or empty",
            )
        if isinstance(source_node_id, list):
            for item in source_node_id:
                if not item:
                    raise build_error(
                        StatusCode.PREGEL_GRAPH_EDGE_INVALID,
                        source_id=source_node_id,
                        target_node_id=target_node_id,
                        reason="source_node_id list has None or empty",
                    )

        if not target_node_id:
            raise build_error(
                StatusCode.PREGEL_GRAPH_EDGE_INVALID,
                source_id=source_node_id,
                target_node_id=target_node_id,
                reason="target_node_id is None or empty",
            )
        self.edges.append((source_node_id, target_node_id))
        self._structure_cache = None
        return self

    def add_conditional_edges(self, source_node_id: str, router: Router) -> Self:
        if not source_node_id:
            raise build_error(
                StatusCode.PREGEL_GRAPH_CONDITION_EDGE_INVALID,
                source_id=source_node_id,
                reason="source_node_is is None or empty",
            )
        if router is None:
            raise build_error(
                StatusCode.PREGEL_GRAPH_CONDITION_EDGE_INVALID, source_id=source_node_id, reason="router is None"
            )

        name = _get_callable_name(router)
        self.branches[source_node_id][name] = Branch(router)
        self._structure_cache = None
        return self

    def register_branch_targets(self, branch_node_id: str, targets: set[str]) -> Self:
        """Register the set of possible targets for a branch node.

        At compile time, wait_for_all nodes query this mapping to determine
        whether their predecessors belong to the same branch (mutually exclusive),
        and merge them into OR-groups accordingly.
        """
        if branch_node_id and targets and len(targets) > 1:
            self.branch_targets[branch_node_id] = targets
            self._structure_cache = None
        return self

    def _forward_reachable(self, start_node: str) -> set[str]:
        """BFS forward search: all nodes reachable from start_node along self.edges."""
        visited = set()
        queue = [start_node]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for src, tgt in self.edges:
                if src == node and isinstance(tgt, str) and tgt not in visited:
                    queue.append(tgt)
        return visited

    def _build_branch_parent(self) -> dict[str, str]:
        """Map nested branch nodes to their parent branch node.

        When a branch exit target is itself a branch node, terminals reachable
        under the inner branch belong to the outer branch path that enters it.
        """
        parent: dict[str, str] = {}
        nested_branch_nodes = set(self.branch_targets) | set(self.branches)
        for branch_node_id, targets in self.branch_targets.items():
            for target in targets:
                if target in nested_branch_nodes:
                    parent[target] = branch_node_id
        return parent

    @staticmethod
    def _branch_root(branch_node_id: str, branch_parent: dict[str, str]) -> str:
        root = branch_node_id
        while root in branch_parent:
            root = branch_parent[root]
        return root

    def _resolve_barrier_groups(self, target_id: str, source_list: list[set[str]]) -> list[set[str]]:
        """Resolve mutually exclusive predecessors into CNF OR-groups.

        Uses a consumer perspective: for each branch target, perform BFS to find
        forward-reachable nodes, then determine which predecessors are exclusive
        to a single branch (OR-group candidates) vs shared/standalone.

        Nested branch nodes are hoisted to their root branch ancestor so that
        terminals from inner OR-groups merge with sibling exits of the outer
        branch instead of being AND-ed across hierarchy levels.
        """
        if not self.branch_targets or not source_list:
            return source_list

        # Step 1: collect all direct predecessors
        all_predecessors = set()
        for g in source_list:
            all_predecessors |= g

        # Step 2: BFS forward search from each branch target
        reachable: dict[tuple[str, str], set[str]] = {}
        for branch_id, targets in self.branch_targets.items():
            for target in targets:
                reachable[(branch_id, target)] = self._forward_reachable(target)

        # Step 3: build ownership info for each predecessor
        pred_info: dict[str, set[tuple[str, str]]] = {}
        for p in all_predecessors:
            pred_info[p] = set()
            for (bid, tgt), nodes in reachable.items():
                if p in nodes:
                    pred_info[p].add((bid, tgt))

        # Step 4: group predecessors by branch_id
        branch_groups: dict[str, set[str]] = defaultdict(set)
        standalone: list[set[str]] = []

        for p in all_predecessors:
            branches = pred_info[p]
            if len(branches) == 1:
                bid = next(iter(branches))[0]
                branch_groups[bid].add(p)
            else:
                standalone.append({p})

        # Step 5: hoist nested branch groups to root branch ancestors
        branch_parent = self._build_branch_parent()
        root_merged: dict[str, set[str]] = defaultdict(set)
        for bid, preds in branch_groups.items():
            root_merged[self._branch_root(bid, branch_parent)] |= preds

        result: list[set[str]] = [group for group in root_merged.values() if group]
        for s in standalone:
            result.append(s)

        return result if result else source_list

    def compile(self, session: BaseSession, **kwargs) -> ExecutableGraph:
        """编译当前图结构为可执行图（纯函数化，可并发复用）。

        同一 PregelGraph 实例可被并发执行安全共享，执行期状态不落在图实例上：
        1. 每节点新建 Vertex 壳包装共享 executable（build 期 Vertex 不再被
           init——规避 Future 绑定旧事件循环、调用计数残留、NodeSession 覆写）；
        2. 无条件全新编译（不再判断 self.pregel is None——channels 上的执行
           残留不跨执行携带）；
        3. after_step 闭包捕获 session 参数（不读 self._session——共享实例
           并发 compile 时会互相覆盖）；
        4. 保留 self.pregel/checkpointer/_session 属性写入，兼容既有读取方。
        """
        fresh_nodes = {}
        for node_id, node in self.nodes.items():
            fresh = Vertex(node_id, node._executable)  # pylint: disable=protected-access
            fresh.is_end_node = node.is_end_node
            fresh.init(session, **kwargs)
            fresh_nodes[node_id] = fresh

        def after_step(loop):
            if session:
                session.state().commit()
            graph_logger.debug(
                f"Finished to run graph super-step [{loop.step}]",
                event_type=LogEventType.GRAPH_SUPER_STEP_END,
                graph_id=loop.config["ns"],
                session_id=loop.config.get(SESSION_ID),
                metadata={"ns": loop.config["ns"], "step": loop.step, "active_nodes": list(loop.active_nodes)},
            )

        checkpointer = session.checkpointer()
        store = GraphStore(checkpointer.graph_store())
        pregel = self._compile(graph_store=store, step_callback=after_step, nodes=fresh_nodes)

        # StreamActor 的 vertex 换成本次执行的 fresh vertex：actor 的
        # should_handle_message/is_done/stream_call 依赖 Vertex.init 设置的
        # _component_ability/_session，build 期 vertex 不再被 init，不替换
        # 则流式边工作流崩溃。ActorManager 为 per-execution 构造，此处
        # 替换不写任何共享对象。
        try:
            manager = session.actor_manager()
        except Exception:
            manager = None
        streams = getattr(manager, "_streams", None)
        if isinstance(streams, dict):
            for actor in streams.values():
                fresh = fresh_nodes.get(getattr(actor, "_node_id", None))
                if fresh is not None:
                    actor.set_vertex(fresh)

        self.pregel = pregel
        self.checkpointer = checkpointer
        self._session = session
        return CompiledGraph(pregel, checkpointer)

    def _compile(self, graph_store=None, step_callback=None, nodes=None) -> Pregel:
        # nodes 注入点：compile 传入本次执行的 fresh vertex（默认仍读 self.nodes）
        nodes = nodes if nodes is not None else self.nodes
        builder = PregelBuilder()
        for node_id, action in nodes.items():
            builder.add_node(node_id, action)

        # 结构解析（edges 分组 + barrier CNF）只依赖构建期结构数据，产物跨
        # compile 复用；重放时 builder 自行拷贝集合/列表输入，不回写缓存产物。
        if self._structure_cache is None:
            self._structure_cache = self._parse_structure()
        barrier_starts, plain_edges = self._structure_cache
        for target_node_id, start in barrier_starts:
            builder.add_edge(start, target_node_id)
        for source_node_id, target_node_id in plain_edges:
            builder.add_edge(source_node_id, target_node_id)

        for start, branches in self.branches.items():
            for name, branch in branches.items():
                builder.add_branch(start, branch.condition)
        return builder.build(graph_store, after_step_callback=step_callback)

    def _parse_structure(self) -> tuple[list[Tuple[str | list[str], str]], list[Tuple[str, list[str | set[str]]]]]:
        """Resolve edges/waits/branch_targets into builder-ready edge specs.

        Pure structural work with no execution-state dependence: barrier
        targets collect their predecessor CNF OR-groups, remaining edges pass
        through unchanged. Invalidated by any structural mutation (see the
        write sites of edges/waits/branches/branch_targets).
        """
        plain_edges: list[Tuple[str | list[str], str]] = []
        sources: dict[str, list[set[str]]] = {}
        for source_node_id, target_node_id in self.edges:
            if target_node_id in self.waits:
                if target_node_id not in sources:
                    sources[target_node_id] = []
                if isinstance(source_node_id, str):
                    sources[target_node_id].append({source_node_id})
                elif isinstance(source_node_id, list):
                    for s in source_node_id:
                        sources[target_node_id].append({s})
            else:
                plain_edges.append((source_node_id, target_node_id))

        # Resolve mutually exclusive predecessors into CNF OR-groups
        for target_node_id in sources:
            sources[target_node_id] = self._resolve_barrier_groups(target_node_id, sources[target_node_id])

        barrier_starts: list[Tuple[str, list[str | set[str]]]] = []
        for target_node_id, groups in sources.items():
            start = [next(iter(g)) if len(g) == 1 else g for g in groups]
            barrier_starts.append((target_node_id, start))
        return barrier_starts, plain_edges

    async def reset(self):
        for node in self.nodes.values():
            await node.reset()


class CompiledGraph(ExecutableGraph):
    def __init__(self, pregel: Pregel, checkpointer: Checkpointer):
        self._pregel = pregel
        self._checkpointer = checkpointer

    async def _invoke(self, inputs: Input, session: BaseSession, config: Any = None) -> Output:
        # 执行树根建立"当前 workflow session"通道：嵌套子工作流/循环体各自的
        # _invoke set 为自己的 session，返回后 token 恢复父 session（栈式）。
        # 供 BranchRouter 等读侧按任务解析，替代共享实例字段。
        # 延迟导入避免 graph ↔ workflow 的循环导入。
        from openjiuwen.core.session.execution_session_ctx import (
            reset_workflow_session,
            set_workflow_session,
        )

        token = set_workflow_session(session)
        try:
            is_main = False
            session_id = session.session_id()
            workflow_id = session.workflow_id()

            if config is None:
                is_main = True
                config = PregelConfig(session_id=session_id, ns=workflow_id, recursion_limit=MAX_RECURSIVE_LIMIT)

            try:
                if is_main:
                    await self._checkpointer.pre_workflow_execute(session, inputs)
                if not isinstance(inputs, InteractiveInput):
                    session.state().commit_user_inputs(inputs)

                result = None
                exception = None

                try:
                    result = await self._pregel.run(config=config)
                except asyncio.CancelledError:
                    graph_logger.debug(
                        "Pregel execution cancelled",
                        event_type=LogEventType.GRAPH_END,
                        metadata={"session_id": session_id, "workflow_id": workflow_id, "cancelled": True},
                    )
                    raise
                except Exception as e:
                    exception = e

                if is_main:
                    await self._checkpointer.post_workflow_execute(session, result, exception)
                elif exception is not None:
                    raise exception
                return result
            except asyncio.CancelledError:
                if is_main:
                    await self._checkpointer.post_workflow_execute(session, {}, None)
                raise
        finally:
            reset_workflow_session(token)

    async def stream(self, inputs: Input, session: Session) -> AsyncIterator[Output]:
        pass

    async def interrupt(self, message: dict):
        return


def _get_callable_name(func) -> str:
    if hasattr(func, "__name__"):
        return func.__name__
    elif hasattr(func, "__class__"):
        return func.__class__.__name__
    else:
        return repr(func)
