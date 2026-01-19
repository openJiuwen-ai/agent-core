# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import inspect
import os
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Self, Union

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow.components.component import ComponentComposable
from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter, WORKFLOW_DRAWABLE
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import Graph, Router, ExecutableGraph

from openjiuwen.core.session import BaseSession, ProxySession
from openjiuwen.core.session import Transformer
from openjiuwen.core.session import WorkflowSession, SubWorkflowSession
from openjiuwen.core.session import RouterSession

from openjiuwen.core.graph.stream_actor.base import StreamGraph
from openjiuwen.core.workflow.workflow_config import WorkflowConfig
from openjiuwen.core.common.schema.workflow_spec import CompIOConfig, NodeSpec
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.graph.graph import PregelGraph

@dataclass
class EdgeTopology:
    """Edge topology context for ability inference."""
    source_map: dict[str, list[str]]
    target_map: dict[str, list[str]]
    source_stream_map: dict[str, list[str]]
    target_stream_map: dict[str, list[str]]

    def all_edge_nodes(self) -> set[str]:
        """Get all nodes referenced in edges."""
        return (
                set(self.source_map.keys()) |
                set(self.target_map.keys()) |
                set(self.source_stream_map.keys()) |
                set(self.target_stream_map.keys())
        )

class ConnectionType(Enum):
    """Type of workflow connection."""
    CONNECTION = "connection"
    STREAM_CONNECTION = "stream_connection"

class BaseWorkflow:
    def __init__(self, workflow_config: WorkflowConfig = None, new_graph: Graph = None):
        self._graph = new_graph if new_graph else PregelGraph()
        self._workflow_config = workflow_config if workflow_config else WorkflowConfig(card=WorkflowCard(id=uuid.uuid4().hex))
        self._workflow_spec = self._workflow_config.spec
        self._stream_actor = StreamGraph()
        self._session = ProxySession()
        self._drawable = None
        drawable = os.environ.get(WORKFLOW_DRAWABLE, "false").lower() == "true"
        if drawable:
            from openjiuwen.core.graph.visualization.drawable import Drawable
            self._drawable = Drawable()

    def config(self):
        return self._workflow_config

    @classmethod
    def _validate_comp_id(cls, comp_id: str) -> None:
        """validate compnent id"""
        if len(comp_id) > 100:
            raise JiuWenBaseException(-1, "workflow component id length must not exceed 100")
        if not re.match(r'^[A-Za-z0-9_-]+$', comp_id):
            raise JiuWenBaseException(-1, "workflow component id must contain only letters (a–z, A–Z), "
                                          "digits (0–9), underscores (_) or hyphens (-)")

    def _validate_connection_comp_ids(self, src_comp_id: str, target_comp_id: str,
                                      connection_type: ConnectionType = ConnectionType.CONNECTION) -> None:
        """Validate that component IDs exist in comp_configs before adding connection.

        This prevents KeyError in _auto_complete_abilities when edges reference non-existent components.
        """
        registered_comps = set(self._workflow_spec.comp_configs.keys())

        missing_comps = []
        if src_comp_id not in registered_comps:
            missing_comps.append(f"source '{src_comp_id}'")
        if target_comp_id not in registered_comps:
            missing_comps.append(f"target '{target_comp_id}'")

        if missing_comps:
            raise JiuWenBaseException(
                StatusCode.WORKFLOW_COMPONENT_CONFIG_ERROR.code,
                f"Cannot add {connection_type.value} from '{src_comp_id}' to '{target_comp_id}': "
                f"component(s) {', '.join(missing_comps)} not registered. "
                f"Please call add_workflow_comp/set_start_comp/set_end_comp first. "
                f"Currently registered components: {sorted(registered_comps) if registered_comps else '(none)'}"
            )

    def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: ComponentComposable,
            *,
            wait_for_all: bool = None,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            comp_ability: list[ComponentAbility] = None
    ) -> Self:
        self._validate_comp_id(comp_id)
        node_spec = NodeSpec(
            io_config=CompIOConfig(inputs_schema=inputs_schema, outputs_schema=outputs_schema),
            stream_io_configs=CompIOConfig(inputs_schema=stream_inputs_schema, outputs_schema=stream_outputs_schema),
            abilities=comp_ability if comp_ability is not None else [])

        for ability in node_spec.abilities:
            if ability in [ComponentAbility.TRANSFORM, ComponentAbility.COLLECT]:
                if wait_for_all is None:
                    wait_for_all = True
                if not wait_for_all:
                    raise JiuWenBaseException(-1, "stream components need to wait for all")
        self._workflow_spec.comp_configs[comp_id] = node_spec
        if wait_for_all is None:
            wait_for_all = False
        workflow_comp.add_component(graph=self._graph, node_id=comp_id, wait_for_all=wait_for_all)

        if self._drawable:
            self._drawable.add_node(comp_id, workflow_comp)
        return self

    def start_comp(
            self,
            start_comp_id: str,
    ) -> Self:
        self._graph.start_node(start_comp_id)

        if self._drawable:
            self._drawable.set_start_node(start_comp_id)
        return self

    def end_comp(
            self,
            end_comp_id: str,
    ) -> Self:
        self._graph.end_node(end_comp_id)

        if self._drawable:
            self._drawable.set_end_node(end_comp_id)
        return self

    def add_connection(self, src_comp_id: Union[str, list[str]], target_comp_id: str) -> Self:
        self._graph.add_edge(src_comp_id, target_comp_id)
        if isinstance(src_comp_id, list):
            for source_id in src_comp_id:
                if source_id not in self._workflow_spec.edges:
                    self._workflow_spec.edges[source_id] = [target_comp_id]
                else:
                    self._workflow_spec.edges[source_id].append(target_comp_id)
                if self._drawable:
                    self._drawable.add_edge(source_id, target_comp_id)
        else:
            if src_comp_id not in self._workflow_spec.edges:
                self._workflow_spec.edges[src_comp_id] = [target_comp_id]
            else:
                self._workflow_spec.edges[src_comp_id].append(target_comp_id)
            if self._drawable:
                self._drawable.add_edge(src_comp_id, target_comp_id)
        return self

    def add_stream_connection(self, src_comp_id: str, target_comp_id: str) -> Self:
        self._graph.add_edge(src_comp_id, target_comp_id)
        stream_executables = self._graph.get_nodes()
        self._stream_actor.add_stream_consumer(stream_executables[target_comp_id], target_comp_id)
        if src_comp_id not in self._workflow_spec.stream_edges:
            self._workflow_spec.stream_edges[src_comp_id] = [target_comp_id]
        else:
            self._workflow_spec.stream_edges[src_comp_id].append(target_comp_id)

        if self._drawable:
            self._drawable.add_edge(src_comp_id, target_comp_id, False, True)
        return self

    def add_conditional_connection(self, src_comp_id: str, router: Router) -> Self:
        if isinstance(router, BranchRouter):
            router.set_session(self._session)
            self._graph.add_conditional_edges(source_node_id=src_comp_id, router=router)
        else:
            def new_router(state):
                sig = inspect.signature(router)
                if 'session' in sig.parameters:
                    return router(session=RouterSession(self._session))
                else:
                    return router()

            self._graph.add_conditional_edges(source_node_id=src_comp_id, router=new_router)

        if self._drawable:
            self._drawable.add_edge(source=src_comp_id, conditional=True, data=router)
        return self

    def compile(self, session: BaseSession, context: ModelContext = None) -> ExecutableGraph:
        if isinstance(session, WorkflowSession):
            session.set_workflow_id(self._workflow_config.card.id)
        session.config().add_workflow_config(self._workflow_config.card.id, self._workflow_config)

        if isinstance(session, SubWorkflowSession):
            main_workflow_config = session.config().get_workflow_config(
                session.main_workflow_id())
            if main_workflow_config is None:
                raise JiuWenBaseException(StatusCode.COMPONENT_SUB_WORKFLOW_RUNTIME_ERROR.code,
                                          StatusCode.COMPONENT_SUB_WORKFLOW_RUNTIME_ERROR.errmsg.format(
                                              error_msg=f"main workflow config is not exit,"
                                                     f" main workflow_id={session.main_workflow_id()}"))
            if session.workflow_nesting_depth() > main_workflow_config.workflow_max_nesting_depth:
                raise JiuWenBaseException(StatusCode.COMPONENT_SUB_WORKFLOW_RUNTIME_ERROR.code,
                                          StatusCode.COMPONENT_SUB_WORKFLOW_RUNTIME_ERROR.errmsg.format(
                                              error_msg=f"workflow nesting hierarchy is too big, must <= "
                                                     f"{main_workflow_config.workflow_max_nesting_depth}"))
        self._session.set_session(session)
        return self._graph.compile(session, context=context)

    @property
    def drawable(self):
        return self._drawable

    def to_mermaid(self, title: str = "", expand_subgraph: int | bool = False, enable_animation: bool = False) -> str:
        if self._drawable:
            return self._drawable.to_mermaid(title=title, expand_subgraph=expand_subgraph,
                                             enable_animation=enable_animation)
        return ""

    def to_mermaid_png(self, title: str = "", expand_subgraph: int | bool = False) -> bytes:
        if self._drawable:
            return self._drawable.to_mermaid_png(title=title, expand_subgraph=expand_subgraph)
        return b""

    def to_mermaid_svg(self, title: str = "", expand_subgraph: int | bool = False) -> bytes:
        if self._drawable:
            return self._drawable.to_mermaid_svg(title=title, expand_subgraph=expand_subgraph)
        return b""

    async def reset(self):
        await self._graph.reset()

    def _auto_complete_abilities(self):
        """Auto-complete component abilities based on edge topology."""
        edge_topology = self._build_edge_topology()
        self._validate_edge_nodes(edge_topology)

        user_provided = self._get_user_provided_abilities()

        self._complete_loop_node_abilities(edge_topology, user_provided)
        self._complete_stream_node_abilities(edge_topology, user_provided)
        self._complete_invoke_abilities(edge_topology, user_provided)

    def _build_edge_topology(self) -> EdgeTopology:
        """Build edge topology context for ability inference."""
        source_map = self._workflow_spec.edges
        source_stream_map = self._workflow_spec.stream_edges
        return EdgeTopology(
            source_map=source_map,
            target_map=self._source_to_target_map(source_map),
            source_stream_map=source_stream_map,
            target_stream_map=self._source_to_target_map(source_stream_map),
        )

    def _validate_edge_nodes(self, edge_topology: EdgeTopology) -> None:
        """DFX: Validate all nodes in edges exist in comp_configs."""
        registered_comps = set(self._workflow_spec.comp_configs.keys())
        all_edge_nodes = edge_topology.all_edge_nodes()
        missing_nodes = all_edge_nodes - registered_comps
        if not missing_nodes:
            return

        edge_details = self._collect_problematic_edges(edge_topology, missing_nodes)
        raise JiuWenBaseException(
            StatusCode.WORKFLOW_COMPONENT_CONFIG_ERROR.code,
            f"Component ID mismatch: nodes {sorted(missing_nodes)} are referenced in edges "
            f"but not registered via add_workflow_comp/set_start_comp/set_end_comp.\n"
            f"Registered components: {sorted(registered_comps)}\n"
            f"Problematic edges:\n" + "\n".join(edge_details)
        )

    @staticmethod
    def _collect_problematic_edges(edge_topology: EdgeTopology, missing_nodes: set) -> list[str]:
        """Collect edge details that reference missing nodes."""
        edge_details = []
        for connection_type, edge_map in [(ConnectionType.CONNECTION, edge_topology.source_map),
                                          (ConnectionType.STREAM_CONNECTION, edge_topology.source_stream_map)]:
            for src, targets in edge_map.items():
                for tgt in targets:
                    if src in missing_nodes or tgt in missing_nodes:
                        edge_details.append(f"  - {connection_type.value}: '{src}' -> '{tgt}'")
        return edge_details

    def _get_user_provided_abilities(self) -> dict[str, bool]:
        """Check which components have user-provided ability configurations."""
        return {
            comp_id: len(comp_conf.abilities) > 0
            for comp_id, comp_conf in self._workflow_spec.comp_configs.items()
        }

    def _complete_loop_node_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete abilities for loop start/end nodes."""
        loop_start_nodes = getattr(self, '_start_nodes', None) or []
        loop_end_nodes = getattr(self, '_end_nodes', None) or []

        for node in loop_start_nodes:
            if not user_provided[node]:
                if node in edge_topology.source_stream_map:
                    self._add_ability_to_node(node, ComponentAbility.STREAM)
                if node in edge_topology.source_map:
                    self._add_ability_to_node(node, ComponentAbility.INVOKE)

        for node in loop_end_nodes:
            if not user_provided[node] and node in edge_topology.target_stream_map:
                self._add_ability_to_node(node, ComponentAbility.COLLECT)

    def _complete_stream_node_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete abilities for stream connection nodes (STREAM/TRANSFORM/COLLECT)."""
        # Nodes that output stream
        for node in edge_topology.source_stream_map:
            if user_provided[node]:
                continue
            # Has regular input + streaming output -> STREAM
            if node in edge_topology.target_map:
                self._add_ability_to_node(node, ComponentAbility.STREAM)
            # Has streaming input + streaming output -> TRANSFORM
            if node in edge_topology.target_stream_map:
                self._add_ability_to_node(node, ComponentAbility.TRANSFORM)

        # Nodes that receive stream
        for node in edge_topology.target_stream_map:
            if user_provided[node]:
                continue
            # Has streaming input + regular output -> COLLECT
            if node in edge_topology.source_map:
                self._add_ability_to_node(node, ComponentAbility.COLLECT)

    def _complete_invoke_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete INVOKE ability for regular connection nodes."""
        for node in edge_topology.target_map:
            if not user_provided[node] and node in edge_topology.source_map:
                self._add_ability_to_node(node, ComponentAbility.INVOKE)

    def _add_ability_to_node(self, comp_id: str, ability: ComponentAbility) -> None:
        """Add ability to a component if not already present."""
        abilities = self._workflow_spec.comp_configs[comp_id].abilities
        if ability not in abilities:
            abilities.append(ability)

    @staticmethod
    def _source_to_target_map(source_map: dict[str, list[str]]) -> dict[str, list[str]]:
        """Convert source->targets map to target->sources map."""
        target_map = {}
        for source, targets in source_map.items():
            for target in targets:
                if target not in target_map:
                    target_map[target] = []
                target_map[target].append(source)
        return target_map