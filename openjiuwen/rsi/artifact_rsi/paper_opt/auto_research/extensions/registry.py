"""Central registry for the pipeline's own OpenJiuwen extensions.

This is the pipeline's reusable toolbox: custom Tool / Rail / Agent subclasses that
extend OpenJiuwen through its public extension points, built up over time as the
pipeline matures. This is distinct from per-experiment generated code (see
`experiments/`), which is produced at runtime by the experiment_design /
experiment_execution agents for a single research task and is not curated here.

Rule: everything in `extensions/` *adds to* OpenJiuwen (new subclasses, new
registered tools/rails/agents). Nothing here should monkeypatch, delete, or
otherwise mutate OpenJiuwen's own classes/modules.
"""

from __future__ import annotations

from typing import Any


def register_all(runtime: Any) -> None:
    """Register all pipeline-owned tools/rails/agents onto an OpenJiuwen runtime.

    Import and register from extensions.tools / extensions.rails / extensions.agents
    here as they're added, e.g.:

        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.my_tool import MyTool
        runtime.register_tool(MyTool())
    """
    raise NotImplementedError
