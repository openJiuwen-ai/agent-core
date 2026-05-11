# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionController P2P 场景示例

点对点（P2P）通信模式下的 SessionController 使用：
  Planner -> Coder -> Reviewer 顺序协作，通过 GlobalSessionController 内置
  callback 机制自动建立 downstream 可见性链。

核心演示：
  1. 配置 RunnerConfig 启用 session_controller
  2. 为每个 Agent 注册 SessionController 并创建会话
  3. P2P 通信时通过 trigger AGENT_P2P_RECEIVED 记录 downstream 关系
  4. 会话数据的读写与跨 Agent 上下文传递
  5. 自动建立的 downstream 单向可见性验证
  6. 调用链可视化
  7. 持久化与清理

参考 runtime_p2p.py 的 P2P 顺序协作设计模式。

Callback 机制说明：
  GlobalSessionController 在初始化时注册了 AGENT_P2P_RECEIVED 回调
  (_on_agent_p2p_received)。当 TeamRuntime 的 MessageRouter 路由 P2P 消息时，
  会触发该回调，自动为 sender 的 session 添加到 recipient 的 downstream 关系。

  在 standalone TeamRuntime 场景下，MessageRouter.route_p2p_message 会在
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

class PlannerAgent(CommunicableAgent, BaseAgent):
    """规划 Agent：接收任务并制定执行计划"""

    def configure(self, config) -> 'PlannerAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        task = inputs.get("task", "") if isinstance(inputs, dict) else str(inputs)
        print(f"[Planner] 收到任务: {task}")

        plan = {"task": task, "steps": ["1. 分析需求", "2. 设计方案", "3. 编写代码"]}
        print(f"[Planner] 制定计划: {plan['steps']}")

        if session:
            session.update_state({"task": task, "plan": plan})
            print(f"[Planner] 会话数据已更新: task={task}")

        return plan

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class CoderAgent(CommunicableAgent, BaseAgent):
    """编码 Agent：根据计划实现代码"""

    def configure(self, config) -> 'CoderAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        steps = inputs.get("steps", []) if isinstance(inputs, dict) else []
        print(f"[Coder]   收到计划，步骤数: {len(steps)}，开始编码...")

        code = "def solution():\n    # 实现代码\n    pass"
        print(f"[Coder]   生成代码:\n          {code}")

        if session:
            session.update_state({"steps": steps, "code": code, "status": "completed"})
            print(f"[Coder]   会话数据已更新: code 已生成")

        return {"code": code, "status": "completed"}

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        result = await self.invoke(inputs, session)
        yield result


class ReviewerAgent(CommunicableAgent, BaseAgent):
    """审查 Agent：审查代码质量"""

    def configure(self, config) -> 'ReviewerAgent':
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        code = inputs.get("code", "") if isinstance(inputs, dict) else str(inputs)
        print(f"[Reviewer] 审查代码，长度: {len(code)} 字符")

        review = {"approved": True, "comments": "代码结构清晰，符合规范"}
        print(f"[Reviewer] 审查结果: {review}")

        if session:
            session.update_state({"code": code, "review": review})
            print(f"[Reviewer] 会话数据已更新: review 已完成")

        return review

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


async def main():
    print("=" * 55)
    print("SessionController P2P 场景示例")
    print("流程: user -> planner -P2P-> coder -P2P-> reviewer")
    print("      downstream 由 callback 自动建立")
    print("=" * 55)

    tmp_path = Path("./tmp_session_p2p")
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
        # 注册了 AGENT_P2P_RECEIVED 回调。当 P2P 消息路由时，
        # _on_agent_p2p_received 会自动为 sender session 添加 downstream。
        # 前提条件：
        #   - RunnerConfig.enable_session_controller = True
        #   - sender agent 已在 GlobalSessionController 中注册
        #   - sender agent 有活跃 session

        s_planner = await _setup_session_for_agent(global_ctrl, "planner")
        s_coder = await _setup_session_for_agent(global_ctrl, "coder")
        s_reviewer = await _setup_session_for_agent(global_ctrl, "reviewer")

        print(f"planner 会话: {s_planner.session_id}, scope={s_planner.session_scope}")
        print(f"coder 会话:   {s_coder.session_id}, scope={s_coder.session_scope}")
        print(f"reviewer 会话: {s_reviewer.session_id}, scope={s_reviewer.session_scope}")

        # --------------------------------------------------
        # 3. P2P 顺序通信流程（callback 自动建立 downstream）
        # --------------------------------------------------
        print("\n--- 3. P2P 顺序通信流程 ---")
        print("    每次 runtime.send 后触发 AGENT_P2P_RECEIVED callback,")
        print("    callback 自动为 sender 添加到 recipient 的 downstream")

        planner_card = AgentCard(id="planner", name="planner", description="任务规划者")
        coder_card = AgentCard(id="coder", name="coder", description="代码实现者")
        reviewer_card = AgentCard(id="reviewer", name="reviewer", description="代码审查者")

        runtime = TeamRuntime()
        runtime.register_agent(planner_card, lambda: PlannerAgent(card=planner_card))
        runtime.register_agent(coder_card, lambda: CoderAgent(card=coder_card))
        runtime.register_agent(reviewer_card, lambda: ReviewerAgent(card=reviewer_card))
        await runtime.start()

        try:
            # Step 1: user -> planner
            plan = await runtime.send(
                message={"task": "实现快速排序算法"},
                recipient="planner",
                sender="user",
            )

            # Step 2: planner -> coder
            code_result = await runtime.send(
                message=plan,
                recipient="coder",
                sender="planner",
                session_id="default_session",
            )

            # Step 3: coder -> reviewer
            review_result = await runtime.send(
                message=code_result,
                recipient="reviewer",
                sender="coder",
                session_id="default_session",
            )

            print("\n--- P2P 通信流程完成 ---")
            print(f"最终审查结果: {review_result}")

        finally:
            await runtime.stop()

        # --------------------------------------------------
        # 4. 验证 callback 自动建立的 downstream 关系
        # --------------------------------------------------
        print("\n--- 4. 验证 callback 建立的 downstream ---")

        planner_ctrl = global_ctrl.get_agent("planner")
        if planner_ctrl:
            planner_session = await planner_ctrl.get_scope_active_session(
                SessionScope(scope=MainScope())
            )
            if planner_session:
                downstreams = planner_session.get_downstreams()
                print(f"planner downstream 数量: {len(downstreams)}")
                for (target_agent, target_session), policy in downstreams.items():
                    print(f"  planner -> {target_agent}/{target_session}... "
                          f"(perm={policy.permission.name})")

        coder_ctrl = global_ctrl.get_agent("coder")
        if coder_ctrl:
            coder_session = await coder_ctrl.get_scope_active_session(
                SessionScope(scope=MainScope())
            )
            if coder_session:
                downstreams = coder_session.get_downstreams()
                print(f"coder downstream 数量: {len(downstreams)}")
                for (target_agent, target_session), policy in downstreams.items():
                    print(f"  coder -> {target_agent}/{target_session[:8]}... "
                          f"(perm={policy.permission.name})")

        # --------------------------------------------------
        # 5. 单向可见性验证
        # --------------------------------------------------
        print("\n--- 5. 单向可见性验证 ---")

        planner_session = await planner_ctrl.get_scope_active_session(
            SessionScope(scope=MainScope())
        ) if planner_ctrl else None
        coder_session = await coder_ctrl.get_scope_active_session(
            SessionScope(scope=MainScope())
        ) if coder_ctrl else None

        if planner_session and coder_session:
            print(f"planner 可见 coder:    {planner_session.can_see('coder', s_coder.session_id)}")
            print(f"coder 可见 planner:    {coder_session.can_see('planner', s_planner.session_id)}")
            print(f"coder 可见自己:        {coder_session.can_see('coder', s_coder.session_id)}")

        # --------------------------------------------------
        # 6. 验证会话数据
        # --------------------------------------------------
        print("\n--- 6. 验证各 Agent 会话数据 ---")

        for agent_id in ["planner", "coder", "reviewer"]:
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

        if planner_session:
            visualization = await GlobalSessionController.visualize_call_chain(
                agent_id="planner",
                session_id=planner_session.session_id,
            )
            print(f"\n{visualization}")

        # --------------------------------------------------
        # 8. 持久化与清理
        # --------------------------------------------------
        print("\n--- 8. 持久化与清理 ---")

        await global_ctrl.flush_all()
        print("flush_all 完成: 所有 Agent 会话数据已写入磁盘")

        meta_map = global_ctrl.get_agent("planner").list_metas()
        for scope, scope_meta in meta_map.items():
            print(f"  planner scope={scope}, 活跃会话={scope_meta.active_session}, "
                  f"总会话数={len(scope_meta.sessions)}")

        removed = await global_ctrl.remove_agent("reviewer")
        print(f"移除 reviewer: {removed}")
        print(f"剩余 Agent: {list(global_ctrl.controllers.keys())}")

        print("\n--- 示例完成 ---")

    finally:
        if tmp_path.exists():
            print(f"\n--- tmp_session_p2p 目录树 ---")
            print(f"{tmp_path.name}/")
            entries = sorted(tmp_path.rglob("*"), key=lambda p: str(p.relative_to(tmp_path)))
            for i, p in enumerate(entries):
                rel = p.relative_to(tmp_path)
                parts = rel.parts
                depth = len(parts) - 1
                is_last_in_parent = True
                parent = p.parent
                siblings = sorted(parent.iterdir(), key=lambda x: x.name)
                if siblings and p != siblings[-1]:
                    is_last_in_parent = False
                prefix = ""
                for d in range(depth):
                    ancestor = tmp_path.joinpath(*parts[:d + 1])
                    ancestor_parent = ancestor.parent
                    anc_siblings = sorted(ancestor_parent.iterdir(), key=lambda x: x.name)
                    if ancestor == anc_siblings[-1]:
                        prefix += "    "
                    else:
                        prefix += "│   "
                connector = "└── " if is_last_in_parent else "├── "
                name = p.name if p.is_file() else f"{p.name}/"
                print(f"{prefix}{connector}{name}")
            shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
