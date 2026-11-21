"""Compose a directory-backed Symphony Runtime with a host-owned native Model."""

from openjiuwen.symphony import PlanningContext, SymphonyConfig, SymphonyRuntime


async def plan_task(model, config: SymphonyConfig, query: str, context: PlanningContext):
    """Select an execution strategy and prepare a Skill graph only when needed."""
    runtime = SymphonyRuntime(model, config)
    try:
        async def prepare_skill_graph():
            status = runtime.orchestration.status()
            if not status.exists or not status.fresh:
                await runtime.orchestration.build()

        result = await runtime.orchestration.plan(query, context=context, prepare_skill_graph=prepare_skill_graph)
        return {"execution_route": result["execution_route"], "planned_graph": result.get("planned_graph")}
    finally:
        await runtime.aclose()


async def build_and_plan(model, capability_root: str, artifact_root: str, query: str):
    config = SymphonyConfig(
        capability={"sources": [{"type": "skill", "root": capability_root}]},
        storage={"artifact_root": artifact_root},
        retrieval={"use_existing_index": True},
    )
    runtime = SymphonyRuntime(model, config)
    try:
        tree = await runtime.retrieval.build()
        if not tree["success"]:
            raise RuntimeError(tree["result"])
        await runtime.orchestration.build()
        runtime.retrieval.bind_search(session_scope="example", pin_index_revision=True)
        candidates = await runtime.retrieval.search(query=query, type="skill")
        plan = await runtime.orchestration.plan(query, type="skill")
        return {"candidates": str(candidates), "planned_graph": plan.to_dict()["planned_graph"]}
    finally:
        await runtime.aclose()


async def plan_expert_tasks(model, config: SymphonyConfig, query: str):
    """Plan expert assignments and inline roles without requiring a built Skill graph."""
    runtime = SymphonyRuntime(model, config)
    try:
        plan = await runtime.orchestration.plan(query, type="expert")
        return plan.to_dict()["planned_graph"]
    finally:
        await runtime.aclose()
