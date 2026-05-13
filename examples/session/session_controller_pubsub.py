# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionController Pub-Sub 场景示例

发布-订阅（Pub-Sub）通信模式下的 SessionController 使用：
  Coordinator 广播任务，多个 Worker 并发处理，Monitor 记录完成情况。
  通过 GlobalSessionController 内置 callback 机制自动建立 downstream 可见性链。

核心演示：
  1. 配置 RunnerConfig 启用 session_controller
  2. 为每个 Agent 注册 SessionController 并创建会话
  3. Pub-Sub 通信时通过 trigger AGENT_PUBSUB_RECEIVED 记录 downstream 关系
  4. 会话数据的读写与跨 Agent 上下文传递
  5. 自动建立的 downstream 可见性验证（一对多扇出）
  6. 调用链可视化
  7. 持久化与清理

参考 runtime_pubsub.py 的 Pub-Sub 广播协作设计模式。

Callback 机制说明：
  GlobalSessionController 在初始化时注册了 AGENT_PUBSUB_RECEIVED 回调
  (_on_agent_pubsub_received)。当 TeamRuntime 的 MessageRouter 路由 Pub-Sub
  消息时，会触发该回调，自动为 sender 的 session 添加到每个 subscriber 的
  downstream 关系。

  在 standalone TeamRuntime 场景下，MessageRouter._invoke_subscriber 会在
  消息路由时触发 callback。本示例中通过显式 trigger callback 来确保
  downstream 关系被正确记录，展示完整的调用链追踪能力。
"""
import asyncio
import shutil
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.multi_agent.team_runtime import TeamRuntime, CommunicableAgent
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import set_runner_config, get_runner_config
from openjiuwen.core.session.session import Session
from openjiuwen.core.session.session_controller import (
    ChainSession,
    GlobalSessionController,
)
from openjiuwen.core.session.session_controller.scope import MainScope, SessionScope
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Agent 定义 - 每个 Agent 在 invoke 中通过 session 更新数据
# ---------------------------------------------------------------------------

class CoordinatorAgent(CommunicableAgent, BaseAgent):
    """协调 Agent：发布任务到 task_events 主题"""

    def configure(self, config) -> 'CoordinatorAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        print(f"[Coordinator] 发布任务: {task}")

        if session:
            session.update_state({"task": task, "status": "published"})
            print(f"[Coordinator] 会话数据已更新: task={task}")

        await self.publish(
            message={"event": "new_task", "task": task, "priority": "high"},
            topic_id="task_events",
        )
        return {"status": "task_published"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class WorkerAgent(CommunicableAgent, BaseAgent):
    """工作 Agent：订阅 task_events，处理后发布完成事件"""

    def __init__(self, card: AgentCard, worker_id: str):
        super().__init__(card=card)
        self.worker_id = worker_id

    def configure(self, config) -> 'WorkerAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        if not isinstance(inputs, dict) or inputs.get("event") != "new_task":
            return {"status": "ignored"}

        task = inputs.get("task", "")
        print(f"[Worker-{self.worker_id}] 收到任务: {task}")

        result = f"Worker-{self.worker_id} 完成: {task}"
        print(f"[Worker-{self.worker_id}] {result}")

        if session:
            session.update_state({"task": task, "result": result, "status": "completed"})
            print(f"[Worker-{self.worker_id}] 会话数据已更新: result={result}")

        await self.publish(
            message={"event": "task_completed", "worker": self.worker_id, "result": result},
            topic_id="completion_events",
        )
        return {"status": "processed"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class MonitorAgent(CommunicableAgent, BaseAgent):
    """监控 Agent：订阅 completion_events，记录完成情况"""

    def configure(self, config) -> 'MonitorAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        if isinstance(inputs, dict) and inputs.get("event") == "task_completed":
            worker = inputs.get("worker")
            result = inputs.get("result")
            print(f"[Monitor]     记录完成: {worker} -> {result}")

            if session:
                completed = session.get_data().get("completed_tasks", [])
                completed.append({"worker": worker, "result": result})
                session.update_state({"completed_tasks": completed})
                print(f"[Monitor]     会话数据已更新: completed_tasks 数量={len(completed)}")

        return {"status": "logged"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _reset_global_controller(base_path: Path) -> GlobalSessionController:
    GlobalSessionController._instances = {}
    ctrl = GlobalSessionController()
    ctrl.base_path = base_path
    ctrl._data_container_type = "agent"
    return ctrl


async def _setup_session_for_agent(
        global_ctrl: GlobalSessionController,
        agent_id: str,
) -> ChainSession:
    _, controller = await global_ctrl.create_if_not_exist_agent(agent_id)
    main_scope = SessionScope(scope=MainScope())
    session_id = "default_session"
    _, session = await controller.create_if_not_exists(main_scope, session_id)
    return session


def _print_tree(root: Path) -> None:
    print(f"{root.name}/")
    entries = sorted(root.rglob("*"), key=lambda p: str(p.relative_to(root)))
    for p in entries:
        rel = p.relative_to(root)
        parts = rel.parts
        depth = len(parts) - 1
        is_last_in_parent = True
        siblings = sorted(p.parent.iterdir(), key=lambda x: x.name)
        if siblings and p != siblings[-1]:
            is_last_in_parent = False
        prefix = ""
        for d in range(depth):
            ancestor = root.joinpath(*parts[:d + 1])
            ancestor_parent = ancestor.parent
            anc_siblings = sorted(ancestor_parent.iterdir(), key=lambda x: x.name)
            if ancestor == anc_siblings[-1]:
                prefix += "    "
            else:
                prefix += "│   "
        connector = "└── " if is_last_in_parent else "├── "
        name = p.name if p.is_file() else f"{p.name}/"
        print(f"{prefix}{connector}{name}")


async def main():
    print("=" * 55)
    print("SessionController Pub-Sub 场景示例")
    print("流程: coordinator --publish--> [worker1, worker2, worker3]")
    print("      workers --publish--> monitor")
    print("      downstream 由 callback 自动建立")
    print("=" * 55)

    tmp_path = Path("./tmp_session_pubsub")
    tmp_path.mkdir(parents=True, exist_ok=True)

    try:
        # --------------------------------------------------
        # 1. 配置 RunnerConfig 启用 session_controller
        # --------------------------------------------------
        print("\n--- 1. 配置 RunnerConfig ---")

        runner_config = get_runner_config()
        runner_config.enable_session_controller = True
        set_runner_config(runner_config)
        print(f"enable_session_controller={runner_config.enable_session_controller}")

        # --------------------------------------------------
        # 2. 初始化 GlobalSessionController 并注册 Agent
        # --------------------------------------------------
        print("\n--- 2. 初始化 GlobalSessionController ---")

        global_ctrl = _reset_global_controller(tmp_path)

        # GlobalSessionController.__init__ 中已通过 _register_team_event_callbacks
        # 注册了 AGENT_PUBSUB_RECEIVED 回调。当 Pub-Sub 消息路由时，
        # _on_agent_pubsub_received 会自动为 sender session 添加到 subscriber 的 downstream。
        # 前提条件：
        #   - RunnerConfig.enable_session_controller = True
        #   - sender agent 已在 GlobalSessionController 中注册
        #   - sender agent 有活跃 session

        s_coordinator = await _setup_session_for_agent(global_ctrl, "coordinator")
        s_worker1 = await _setup_session_for_agent(global_ctrl, "worker1")
        s_worker2 = await _setup_session_for_agent(global_ctrl, "worker2")
        s_worker3 = await _setup_session_for_agent(global_ctrl, "worker3")
        s_monitor = await _setup_session_for_agent(global_ctrl, "monitor")

        print(f"coordinator 会话: {s_coordinator.session_id}, scope={s_coordinator.session_scope}")
        print(f"worker1 会话:     {s_worker1.session_id}, scope={s_worker1.session_scope}")
        print(f"worker2 会话:     {s_worker2.session_id}, scope={s_worker2.session_scope}")
        print(f"worker3 会话:     {s_worker3.session_id}, scope={s_worker3.session_scope}")
        print(f"monitor 会话:     {s_monitor.session_id}, scope={s_monitor.session_scope}")

        # --------------------------------------------------
        # 3. Pub-Sub 广播通信流程（callback 自动建立 downstream）
        # --------------------------------------------------
        print("\n--- 3. Pub-Sub 广播通信流程 ---")
        print("    每次 runtime.publish 后触发 AGENT_PUBSUB_RECEIVED callback,")
        print("    callback 自动为 sender 添加到每个 subscriber 的 downstream")

        coordinator_card = AgentCard(id="coordinator", name="coordinator", description="任务协调者")
        worker1_card = AgentCard(id="worker1", name="worker1", description="工作者1")
        worker2_card = AgentCard(id="worker2", name="worker2", description="工作者2")
        worker3_card = AgentCard(id="worker3", name="worker3", description="工作者3")
        monitor_card = AgentCard(id="monitor", name="monitor", description="任务监控者")

        runtime = TeamRuntime()
        runtime.register_agent(coordinator_card, lambda: CoordinatorAgent(card=coordinator_card))
        runtime.register_agent(worker1_card, lambda: WorkerAgent(card=worker1_card, worker_id="1"))
        runtime.register_agent(worker2_card, lambda: WorkerAgent(card=worker2_card, worker_id="2"))
        runtime.register_agent(worker3_card, lambda: WorkerAgent(card=worker3_card, worker_id="3"))
        runtime.register_agent(monitor_card, lambda: MonitorAgent(card=monitor_card))

        await runtime.subscribe("worker1", "task_events")
        await runtime.subscribe("worker2", "task_events")
        await runtime.subscribe("worker3", "task_events")
        await runtime.subscribe("monitor", "completion_events")

        await runtime.start()

        try:
            await runtime.publish(
                message={"event": "new_task", "task": "处理数据批次", "priority": "high"},
                topic_id="task_events",
                sender="coordinator",
            )
            await asyncio.sleep(1)

            print("\n--- Pub-Sub 通信流程完成 ---")
        finally:
            await runtime.stop()

        # --------------------------------------------------
        # 4. 验证 callback 自动建立的 downstream 关系
        # --------------------------------------------------
        print("\n--- 4. 验证 callback 建立的 downstream ---")

        coordinator_ctrl = global_ctrl.get_agent("coordinator")
        if coordinator_ctrl:
            coordinator_session = await coordinator_ctrl.get_scope_active_session(
                SessionScope(scope=MainScope())
            )
            if coordinator_session:
                downstreams = coordinator_session.get_downstreams()
                print(f"coordinator downstream 数量: {len(downstreams)}")
                for (target_agent, target_session), policy in downstreams.items():
                    print(f"  coordinator -> {target_agent}/{target_session[:8]}... "
                          f"(perm={policy.permission.name})")

        for worker_id in ["worker1", "worker2", "worker3"]:
            worker_ctrl = global_ctrl.get_agent(worker_id)
            if worker_ctrl:
                worker_session = await worker_ctrl.get_scope_active_session(
                    SessionScope(scope=MainScope())
                )
                if worker_session:
                    downstreams = worker_session.get_downstreams()
                    print(f"{worker_id} downstream 数量: {len(downstreams)}")
                    for (target_agent, target_session), policy in downstreams.items():
                        print(f"  {worker_id} -> {target_agent}/{target_session[:8]}... "
                              f"(perm={policy.permission.name})")

        # --------------------------------------------------
        # 5. 可见性验证
        # --------------------------------------------------
        print("\n--- 5. 可见性验证 ---")

        coordinator_session = await coordinator_ctrl.get_scope_active_session(
            SessionScope(scope=MainScope())
        ) if coordinator_ctrl else None
        worker1_ctrl = global_ctrl.get_agent("worker1")
        worker1_session = await worker1_ctrl.get_scope_active_session(
            SessionScope(scope=MainScope())
        ) if worker1_ctrl else None
        monitor_ctrl = global_ctrl.get_agent("monitor")
        monitor_session = await monitor_ctrl.get_scope_active_session(
            SessionScope(scope=MainScope())
        ) if monitor_ctrl else None

        if coordinator_session and worker1_session:
            print(f"coordinator 可见 worker1:  {coordinator_session.can_see('worker1', s_worker1.session_id)}")
            print(f"worker1 可见 coordinator:  {worker1_session.can_see('coordinator', s_coordinator.session_id)}")

        if worker1_session and monitor_session:
            print(f"worker1 可见 monitor:      {worker1_session.can_see('monitor', s_monitor.session_id)}")
            print(f"monitor 可见 worker1:      {monitor_session.can_see('worker1', s_worker1.session_id)}")

        # --------------------------------------------------
        # 6. 验证会话数据
        # --------------------------------------------------
        print("\n--- 6. 验证各 Agent 会话数据 ---")

        for agent_id in ["coordinator", "worker1", "worker2", "worker3", "monitor"]:
            controller = global_ctrl.get_agent(agent_id)
            if controller:
                active = await controller.get_scope_active_session(
                    SessionScope(scope=MainScope())
                )
                if active:
                    data = active.get_data()
                    print(f"[{agent_id}] 会话数据: {data}")

        # --------------------------------------------------
        # 7. 调用链可视化
        # --------------------------------------------------
        print("\n--- 7. 调用链可视化 ---")

        if coordinator_session:
            visualization = await GlobalSessionController.visualize_call_chain(
                agent_id="coordinator",
                session_id=coordinator_session.session_id,
            )
            print(f"\n{visualization}")

        # --------------------------------------------------
        # 8. 持久化与清理
        # --------------------------------------------------
        print("\n--- 8. 持久化与清理 ---")

        await global_ctrl.flush_all()
        print("flush_all 完成: 所有 Agent 会话数据已写入磁盘")

        meta_map = global_ctrl.get_agent("coordinator").list_metas()
        for scope, scope_meta in meta_map.items():
            print(f"  coordinator scope={scope}, 活跃会话={scope_meta.active_session}, "
                  f"总会话数={len(scope_meta.sessions)}")

        removed = await global_ctrl.remove_agent("monitor")
        print(f"移除 monitor: {removed}")
        print(f"剩余 Agent: {list(global_ctrl.controllers.keys())}")

        print("\n--- 示例完成 ---")

    finally:
        if tmp_path.exists():
            print(f"\n--- tmp_session_pubsub 目录树 ---")
            _print_tree(tmp_path)
            shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
