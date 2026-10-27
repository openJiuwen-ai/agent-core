# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""执行期 session 的 ContextVar 通道（静态图实例复用的配套机制）。

Workflow 实例复用后，图结构/组件实例跨执行共享，执行期状态不能再落在
可复用对象的实例属性上。两个通道按"谁写入、谁读取"划分：

1. ``_workflow_session``：``CompiledGraph._invoke`` 入口 set、finally
   reset，值为传入 ``_invoke`` 的 session——主/子工作流路径为
   WorkflowSession / SubWorkflowSession，循环体路径
   （AdvancedLoopComponent.on_invoke 以 NodeSession 调 compile/_invoke）
   为 NodeSession。子工作流嵌套经 token 栈式恢复（子执行期间组件解析到
   子 session）。供 ``BranchRouter`` 的路由第二优先级解析。

2. ``_component_exec``：(session, context) 二元组。分支路由属主组件
   （BranchComponent）在 invoke 入口 set 且**不 reset**——路由 dispatch
   发生在 invoke 返回之后的同一 NodeTask 里（先跑节点函数再 dispatch），
   值必须存活到 dispatch。NodeTask 每节点独立 context
   （asyncio.create_task 拷贝），天然跨执行隔离。

   值类型契约：写入端存**组件面 Session 包装器原样**（组件自身读点依赖
   包装器 API）；消费方（分支条件 → AtomicNode._validate_session_and_state）
   要求 ``isinstance(session, BaseSession)``——由 ``resolve_routing_session``
   读取端解包承担（``getattr(session, "_inner", session)``，与
   ``BranchRouter.set_session`` 解包语义一致；NodeSession 自身无
   ``_inner``，不会过度解包）。

前提与已知限制：**per-node 同 task**——组件级值仅在"节点函数与
dispatch 同 task"时可达。vertex 有同 task 直 await 分支（timeout < 0
等），其余经 ``asyncio.wait_for`` 新建子 task，invoke 内的 set 留在子
task 上下文、dispatch 读不到 → 回退 workflow 级解析（粒度降级，非严格
等价；条件求值安全）。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Optional, Tuple

_workflow_session: ContextVar[Optional[Any]] = ContextVar(
    "openjiuwen_workflow_session", default=None
)
_component_exec: ContextVar[Tuple[Optional[Any], Optional[Any], Optional[Any]]] = ContextVar(
    "openjiuwen_component_exec", default=(None, None, None)
)


def set_workflow_session(session: Any) -> Token:
    """设置当前 workflow 级执行 session，返回 token 用于 reset。"""
    return _workflow_session.set(session)


def reset_workflow_session(token: Token) -> None:
    """恢复 workflow 级执行 session 到 set 之前的状态。"""
    _workflow_session.reset(token)


def current_workflow_session() -> Optional[Any]:
    """获取当前 workflow 级执行 session，无则返回 None。"""
    return _workflow_session.get()


def set_component_exec(session: Any, context: Any, owner: Any = None) -> Token:
    """设置当前组件级执行上下文 (session, context, owner)，返回 token。

    分支路由属主（BranchComponent / IntentDetectionComponent）在 invoke
    入口调用且不 reset——dispatch 发生在 invoke 返回后的同一 NodeTask 内。

    :param owner: 写入该值的**路由属主组件**（建议传组件持有的 router 实例）。
        读取侧 ``resolve_routing_session`` 传入 ``owner`` 时，仅当两侧
        owner 一致才采信组件级值；不一致（例如同 task 内前一个组件遗留的
        旧值）则降级到 workflow 级解析。不传 owner 时保持"无条件采信"
        的旧行为。
    """
    return _component_exec.set((session, context, owner))


def reset_component_exec(token: Token) -> None:
    """恢复组件级执行上下文。"""
    _component_exec.reset(token)


def current_component_exec() -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    """获取当前组件级执行上下文 (session, context, owner)。"""
    return _component_exec.get()


def resolve_routing_session(fallback: Any = None, owner: Any = None) -> Any:
    """路由 session 解析链：组件级 → workflow 级 → 调用方回退。

    与两类注册方式语义对齐：
    - ``add_conditional_edges`` 注册的路由 → ProxySession（workflow
      级），对应 workflow 级分支；
    - 路由属主组件每次执行 set_session(组件面 session) → 对应组件级
      分支（需存活到同 task 内的 dispatch）。

    组件级值按组件面 Session 包装器存储；本函数读取端负责解包为
    BaseSession（主机制，非兜底）。workflow 级值同理：执行树根写入的
    也可能是 checkpoint 等场景的 session 只读视图（如
    ``_CheckpointScopedSessionView``），经 ``_inner`` 还原为原始 session，
    与组件级解包语义对称；无 ``_inner`` 的值（WorkflowSession /
    SubWorkflowSession / NodeSession）原样返回，不受影响。

    :param owner: 读取方 router 实例。传入时做**归属校验**——仅当组件级
        值由同一 router（或同一 router 的属主组件）写入才采信，避免同一
        task 上下文内跨执行/跨组件的旧值被误读。
    """
    session, _, comp_owner = _component_exec.get()
    # 归属校验：读取方未指定 owner、组件级值无属主、或两侧属主一致时才采信
    owner_matched = owner is None or comp_owner is None or comp_owner is owner
    if session is not None and owner_matched:
        return getattr(session, "_inner", session)
    workflow_session = _workflow_session.get()
    if workflow_session is not None:
        return getattr(workflow_session, "_inner", workflow_session)
    return fallback
