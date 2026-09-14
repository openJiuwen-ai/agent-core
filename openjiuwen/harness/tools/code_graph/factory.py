# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Profile-driven factory for Code Graph tools.

The public product has two profiles: ``off`` (no graph tools) and ``graph``
(find_* retrieval). Product omits ``submit_code_context`` so the agent edits
after locating. Eval scripts pass ``prompt_mode=locate`` to add submit and
ContextBench exam wording.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.harness.schema.code_graph import (
    PROMPT_MODE_LOCATE,
    PROMPT_MODE_PRODUCT,
    RETRIEVAL_INTERFACE_DEFAULT,
    CodeGraphProfile,
    CodeGraphRetrievalInterface,
    CodeGraphRunState,
    resolve_code_graph_profile,
    resolve_code_graph_retrieval_interface,
)
from openjiuwen.harness.tools.code_graph._base import CodeGraphToolContext
from openjiuwen.harness.tools.code_graph.find import (
    FindBaseClassesTool,
    FindCalleesTool,
    FindCallersTool,
    FindCodeSymbolsTool,
    FindImportersTool,
    FindSubclassesTool,
    InspectCodeStructureTool,
    ReadSymbolTool,
    ResolveSymbolTool,
    SearchSourceTextTool,
    SelectCodeContextTool,
    SubmitCodeContextTool,
    TraceCallPathsTool,
)
from openjiuwen.harness.tools.code_graph.focus_code import FocusCodeTool
from openjiuwen.harness.tools.code_graph.read_code import ReadCodeTool

LOCATE_EXAM_TOOL_NAMES = (
    "resolve_symbol",
    "find_code_symbols",
    "search_source_text",
    "inspect_code_structure",
    "read_symbol",
    "read_code",
    "find_callers",
    "find_callees",
    "find_importers",
    "find_base_classes",
    "find_subclasses",
    "trace_call_paths",
    "select_code_context",
    "submit_code_context",
)

# Product graph: locate then edit. Eval locate-exam adds submit_code_context.
PRODUCT_GRAPH_TOOL_NAMES = tuple(
    name for name in LOCATE_EXAM_TOOL_NAMES if name != "submit_code_context"
)

# ACI product surface. Duplicate read tools stay in classic only; focus_code
# is the source window. Relation hops are opt-in via focus_code.include_relations.
FOCUSED_CORE_TOOL_NAMES = (
    "resolve_symbol",
    "find_code_symbols",
    "search_source_text",
    "inspect_code_structure",
    "focus_code",
)

_TOOL_CLASSES = {
    "read_code": ReadCodeTool,
    "resolve_symbol": ResolveSymbolTool,
    "find_code_symbols": FindCodeSymbolsTool,
    "search_source_text": SearchSourceTextTool,
    "inspect_code_structure": InspectCodeStructureTool,
    "read_symbol": ReadSymbolTool,
    "find_callers": FindCallersTool,
    "find_callees": FindCalleesTool,
    "find_importers": FindImportersTool,
    "find_base_classes": FindBaseClassesTool,
    "find_subclasses": FindSubclassesTool,
    "trace_call_paths": TraceCallPathsTool,
    "select_code_context": SelectCodeContextTool,
    "submit_code_context": SubmitCodeContextTool,
    "focus_code": FocusCodeTool,
}


def code_graph_profile_tool_names(
    profile: Any,
    *,
    prompt_mode: str = PROMPT_MODE_PRODUCT,
    retrieval_interface: Any = RETRIEVAL_INTERFACE_DEFAULT,
) -> tuple[str, ...]:
    """Tool names a profile exposes, in prompt order."""
    resolved = resolve_code_graph_profile(profile)
    if resolved == CodeGraphProfile.OFF:
        return ()
    mode = (prompt_mode or PROMPT_MODE_PRODUCT).strip().lower()
    if mode == PROMPT_MODE_LOCATE:
        return LOCATE_EXAM_TOOL_NAMES
    if (
        resolve_code_graph_retrieval_interface(retrieval_interface)
        == CodeGraphRetrievalInterface.FOCUSED
    ):
        return FOCUSED_CORE_TOOL_NAMES
    return PRODUCT_GRAPH_TOOL_NAMES


def build_code_graph_profile_tools(
    context: CodeGraphToolContext,
    run_state: CodeGraphRunState | None = None,
    *,
    profile: Any = CodeGraphProfile.OFF,
    prompt_mode: str = PROMPT_MODE_PRODUCT,
    retrieval_interface: Any = RETRIEVAL_INTERFACE_DEFAULT,
) -> list:
    """Instantiate the tools for one profile.

    All tools share ``context``, therefore one service, one index, and one run
    state per host agent. Product omits ``submit_code_context``; locate-exam
    (ContextBench) includes it.     ``focused`` replaces ``select_code_context`` / ``read_symbol`` / ``read_code``
    with ``focus_code`` and hides advanced relation tools.
    """
    resolved = resolve_code_graph_profile(profile)
    if resolved == CodeGraphProfile.OFF:
        return []
    resolved_mode = (prompt_mode or PROMPT_MODE_PRODUCT).strip().lower()
    resolved_interface = resolve_code_graph_retrieval_interface(retrieval_interface)
    if run_state is not None:
        run_state.profile = resolved.value
        run_state.prompt_mode = resolved_mode
        run_state.retrieval_interface = resolved_interface.value
        context.run_state = run_state
    names = code_graph_profile_tool_names(
        resolved,
        prompt_mode=resolved_mode,
        retrieval_interface=resolved_interface,
    )
    return [_TOOL_CLASSES[name](context) for name in names]
