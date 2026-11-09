"""Compose a directory-backed Symphony Runtime with a host-owned native Model."""

from openjiuwen.symphony import SymphonyConfig, SymphonyRuntime


async def build_and_plan(model, capability_root: str, artifact_root: str, query: str):
    config = SymphonyConfig(
        capability={"root": capability_root, "type": "skill"},
        storage={"artifact_root": artifact_root},
        retrieval={"discovery": {"use_existing_index": True}},
        flow={"enabled": False},
    )
    runtime = SymphonyRuntime(model, config)
    try:
        tree = await runtime.retrieval.build()
        if not tree["success"]:
            raise RuntimeError(tree["result"])
        await runtime.orchestration.build()
        session = runtime.retrieval.create_session(session_scope="example", pin_index_revision=True)
        candidates = await session.skill_index(operation="search", query=[query])
        plan = await runtime.orchestration.plan(query)
        return {"candidates": str(candidates), "planned_graph": plan.to_dict()["planned_graph"]}
    finally:
        await runtime.aclose()
