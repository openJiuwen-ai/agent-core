# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real-model Team SDK/Protocol regression with JiuwenSwarm and JiuwenBox.

Run in an isolated JiuwenSwarm environment with edit_file=ask and host fallback
disabled. Pass a private model JSON, artifact directory and loopback Box URL.
Cases use the real model, permission rail, SQLite mailbox and sandbox file tool.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.context import set_session_id
from openjiuwen.agent_teams.messager import InProcessMessager, MessagerTransportConfig
from openjiuwen.agent_teams.schema.blueprint import StorageSpec, TeamAgentSpec, TransportSpec
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_member import ApproveToolCallTool
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import Session
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec, SysOperationSpec, TeamModelConfig, WorkspaceSpec


async def wait_for(predicate, timeout=150):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.1)


async def run(args):
    import jiuwenswarm
    import openjiuwen
    from jiuwenswarm.agents.swarm.assembly import enrich_team_spec_for_swarm
    from jiuwenswarm.common.config import get_config
    from jiuwenswarm.server.runtime.agent_adapter.sysop_builder import create_sandbox_sysop_card

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    workspace.mkdir()
    target = workspace / "slide_01.html"
    before = "<html><body><li>确认营销费用</li></body></html>\n"
    target.write_text(before)
    after = before.replace("确认营销费用", "确认费用")
    evidence = {"case": args.case, "status": "FAIL", "sdk": openjiuwen.__file__,
                "consumer": jiuwenswarm.__file__, "writes": []}
    config = get_config()
    assert config["permissions"]["enabled"]
    assert config["permissions"]["tools"]["edit_file"] == "ask"
    assert config["sandbox"]["fallback_on_failure"] is False
    model = json.loads(args.model_config.read_text())
    model["model_request_config"]["model"] = model["model_client_config"]["model_name"]
    model["model_request_config"]["temperature"] = 0
    team_model = TeamModelConfig(**model)
    sid = root.name
    set_session_id(sid)
    await Runner.start()
    agent = None
    stream = None
    transport = None
    try:
        card = create_sandbox_sysop_card(
            args.box_url, "jiuwenbox", project_dir=workspace, is_code_agent=True,
            startup_mode="internal", fallback_on_failure=False,
        )
        assert card is not None
        operation = SysOperationSpec(
            id=sid, mode=card.mode, work_config=card.work_config, gateway_config=card.gateway_config,
        )
        fs = operation.resolve().fs()
        evidence["provider"] = type(fs).__name__
        assert "sandbox" in type(fs).__module__
        assert (await fs.read_file(str(target))).code == 0
        marker = workspace / "provider-marker.txt"
        assert (await fs.write_file(str(marker), "BOX")).code == 0
        assert marker.read_text().strip() == "BOX"
        forbidden = root / "outside-workspace.txt"
        assert (await fs.write_file(str(forbidden), "DENIED")).code != 0
        assert not forbidden.exists()
        original_write = fs.write_file

        async def observe_write(*values, **kwargs):
            result = await original_write(*values, **kwargs)
            path = values[0] if values else kwargs.get("path", kwargs.get("file_path"))
            if str(path) == str(target):
                evidence["writes"].append({"code": result.code})
            return result

        fs.write_file = observe_write
        member = DeepAgentSpec(
            model=team_model, sys_operation=operation, workspace=WorkspaceSpec(root_path=str(workspace)),
            system_prompt="先读指定文件，只用 edit_file 修改指定文字。审批挂起时等待审批。拒绝后保持文件原样，不重试，不换工具。完成后报告结果。",
            max_iterations=8, enable_task_planning=False,
        )
        spec = TeamAgentSpec(
            agents={"leader": member.model_copy(deep=True), "teammate": member}, team_name=sid,
            workspace={"root_path": str(workspace)}, spawn_mode="inprocess",
            transport=TransportSpec(type="inprocess"), external_transport=TransportSpec(type="inprocess"),
            enable_permissions=True, evolution_enabled=False, stale_claim_idle_timeout=1,
            storage=StorageSpec(type="sqlite", params={"connection_string": str(root / "team.sqlite")}),
        )
        enrich_team_spec_for_swarm(spec, session_id=sid, mode="code.team", project_dir=str(workspace), channel_id="sdk")
        context = TeamRuntimeContext(
            role=TeamRole.TEAMMATE, member_name="ppt-designer",
            team_spec=TeamSpec(team_name=sid, display_name=sid, leader_member_name="leader",
                               workspace=spec.workspace.model_dump(), evolution_enabled=False),
            messager_config=spec.transport.build(), db_config=spec.resolve_db_config(), member_model=team_model,
        )
        agent = TeamAgent(AgentCard(id=sid, name="ppt-designer")).configure(spec, context)
        backend = agent.infra.team_backend
        await backend.db.initialize()
        await backend.db.create_cur_session_tables()
        transport = InProcessMessager(config=MessagerTransportConfig(backend="inprocess", node_id="leader"))
        await transport.start()
        leader = TeamBackend(sid, "leader", True, backend.db, transport, evolution_enabled=False)
        await leader.build_team("Slide repair", "Edit one phrase", "Leader", "SDK operator")
        assert await leader.spawn_member("ppt-designer", "ppt-designer", AgentCard(id=sid, name="ppt-designer"),
                                         status=MemberStatus.READY)
        task = await leader.task_manager.add("修正幻灯片文案", "把确认营销费用替换为确认费用")
        assert task.ok and await leader.task_manager.assign(task.task_id, "ppt-designer")
        query = f"请读取 {target}，然后使用 edit_file 将其中的“确认营销费用”替换为“确认费用”。只改这一处，不使用其他写入工具。"
        evidence["prompt_sha256"] = hashlib.sha256(query.encode()).hexdigest()

        async def consume():
            async for frame in agent.stream({"query": query}, session=Session(session_id=sid)):
                with (root / "stream.jsonl").open("a") as output:
                    output.write(json.dumps(frame, default=str, ensure_ascii=False) + "\n")

        stream = asyncio.create_task(consume())
        await wait_for(agent.has_pending_interrupt)
        await wait_for(lambda: not agent.has_in_flight_round(), 20)
        pending = agent.harness._interrupt_session().get_state(INTERRUPTION_KEY)
        ids = [key for entry in pending.interrupted_tools.values() for key in entry.interrupt_requests]
        assert len(ids) == 1
        call_id = ids[0]
        evidence["call_id"] = call_id
        assert target.read_text() == before and not evidence["writes"]
        # Simulate missing in-memory leader metadata while the DB retains it.
        context.team_spec.leader_member_name = None
        backend.leader_member_name = ""
        backend._leader_name_cache = None
        if args.hold_seconds:
            await leader.message_manager.send_message(
                "当前任务继续等待审批；若审批被拒绝，请保持文件原样，不要重试。", "ppt-designer",
            )
            await asyncio.sleep(args.hold_seconds)
            assert agent.has_pending_interrupt() and not agent.has_in_flight_round()
            assert target.read_text() == before and not evidence["writes"]
        original_publish = transport.publish

        async def controlled_publish(*values, **kwargs):
            event = kwargs.get("message")
            if event.event_type == "tool_approval_result":
                evidence["approval_event_seen"] = True
                if args.case != "event-null":
                    evidence["approval_event_dropped"] = True
                    return
            return await original_publish(*values, **kwargs)

        transport.publish = controlled_publish
        approved = args.case != "reject"
        if args.case == "legacy-null":
            # Historical nullable row, persisted before this consumer version.
            legacy = {"type": "tool_approval_result", "tool_call_id": call_id, "approved": approved,
                      "feedback": None, "auto_confirm": None}
            await leader.message_manager.send_message(json.dumps(legacy), "ppt-designer", protocol="json")
        else:
            tool = ApproveToolCallTool(leader, make_translator("en"))
            result = await tool.invoke({
                "member_name": "ppt-designer",
                "tool_call_id": call_id,
                "approved": approved,
                "feedback": None if approved else "拒绝修改，请保持文件原样，不要重试。",
                "auto_confirm": None,
            })
            assert result.success
        await wait_for(lambda: not agent.has_pending_interrupt() and not agent.has_in_flight_round())
        await asyncio.sleep(35)  # Exercise the next natural mailbox poll and duplicate durable reply.
        assert not agent.has_pending_interrupt() and not agent.has_in_flight_round()
        assert target.read_text() == (after if approved else before)
        assert evidence["writes"] == ([{"code": 0}] if approved else [])
        if args.case in {"mailbox-null", "reject"}:
            assert evidence.get("approval_event_dropped") is True
        if args.case == "event-null":
            assert evidence.get("approval_event_seen") is True
        evidence.update(status="PASS", pending=False, file_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        if stream.done():
            await stream
    finally:
        (root / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        if agent is not None:
            await asyncio.wait_for(agent.stop_coordination(), 20)
        if stream is not None:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        if transport is not None:
            await transport.stop()
        await asyncio.wait_for(Runner.stop(), 20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-config", required=True, type=Path)
    parser.add_argument("--box-url", required=True)
    parser.add_argument("--case", choices=["event-null", "mailbox-null", "legacy-null", "reject"], required=True)
    parser.add_argument("--hold-seconds", type=int, default=0)
    asyncio.run(run(parser.parse_args()))
