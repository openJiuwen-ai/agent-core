# coding: utf-8
"""Rails for TeamAgent coordination."""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Optional


from openjiuwen.agent_teams import Messager
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt import InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.harness.rails import ConfirmInterruptRail
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload
from openjiuwen.harness.rails.interrupt.interrupt_base import (
    InterruptDecision,
)
from openjiuwen.core.common.logging import team_logger


class FirstIterationGate(AgentRail):
    """Signals when the agent enters its first task-loop iteration.

    External code can ``await gate.wait()`` to block until
    the agent is actually inside its loop and ready to
    receive steer / follow_up inputs.
    """

    def __init__(self) -> None:
        super().__init__()
        self._event = asyncio.Event()

    async def wait(self) -> None:
        """Block until the first iteration has started."""
        await self._event.wait()

    @property
    def is_ready(self) -> bool:
        return self._event.is_set()

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        if not self._event.is_set():
            self._event.set()

    def reset(self) -> None:
        """Reset the gate for a new round."""
        self._event.clear()


# ============== Tool Approval Rail ==============

class TeamToolApprovalRail(ConfirmInterruptRail):
    """Tool approval rail for team coordination.

    当 teammate 调用工具时，向 leader 发送审批请求消息，
    leader 收到消息后使用 ToolApprove 工具进行回复。

    审批请求包含以下信息：
    - 成员 ID (member_id)
    - 工具名称 (tool_name)
    - 工具调用 ID (tool_call_id)
    - 工具参数 (tool_args)

    Leader 通过调用 ToolApprove 工具传回审批结果：
    - approved: True 表示批准，False 表示拒绝
    - feedback: 可选的反馈信息
    - auto_confirm: 可选，True 表示后续相同工具调用自动批准

    Leader 的审批回复中可以包含 auto_confirm 字段，
    设置后，后续该成员的该工具调用将自动批准，无需再次审批。

    Usage:
        # 在 teammate 端注册
        rail = TeamToolApprovalRail(
            team_id="team_001",
            member_id="member_001",
            leader_id="leader_001",
            db=team_db,
            messager=messager_instance,
            tool_names=["delete_file", "execute_command"],
        )
        await agent.register_rail(rail)

    Flow:
        1. Teammate 调用工具 -> rail 拦截
        2. 检查 auto_confirm_config（用户输入），若配置则直接批准
        3. 未配置自动批准：发送审批请求消息给 leader
        4. 中断等待 leader 的审批响应
        5. Leader 通过 ToolApprove 工具回复 -> resume -> 批准/拒绝
    """

    def __init__(
        self,
        team_id: str,
        member_id: str,
        db: TeamDatabase,
        messager: Messager,
        leader_id: str,
        tool_names: Optional[Iterable[str]] = None,
    ):
        super().__init__(tool_names=tool_names)
        self.team_id = team_id
        self.member_id = member_id
        self.leader_id = leader_id
        self.message_manager = TeamMessageManager(team_id=team_id, member_id=member_id, db=db, messager=messager)

    async def resolve_interrupt(
            self,
            ctx: AgentCallbackContext,
            tool_call: Optional[ToolCall],
            user_input: Optional[Any],
            auto_confirm_config: Optional[dict] = None,
    ) -> InterruptDecision:
        """Resolve tool approval interrupt with team coordination.

        Flow:
        1. First call (user_input is None): Send approval request to leader, interrupt
        2. Resume call (user_input provided): Parse leader's response, decide

        The approval request includes:
        - tool_call_id: Unique identifier for this tool call
        - tool_name: Name of the tool being called
        - tool_args: Arguments passed to the tool (optional)

        Leader responds via session state or user_input with:
        - approved: boolean
        - feedback: optional string
        """

        if tool_call:
            tool_name = tool_call.name
        else:
            team_logger.error(f"tool_call not provided for member {self.member_id}")
            return self.reject(tool_result="Invalid tool call")

        # First call: send approval request to leader and interrupt
        if user_input is None:
            # Check auto-confirm first
            auto_confirm_key = self._get_auto_confirm_key(tool_call)
            if self._is_auto_confirmed(auto_confirm_config, auto_confirm_key):
                team_logger.debug(f"Tool {tool_name} auto-approved for member {self.member_id}")
                return self.approve()

            tool_call_id = self._resolve_tool_call_id(tool_call)

            if tool_call.arguments:
                args_str = tool_call.arguments
            else:
                args_str = "{}"
            message = (
                "Teammate tool approval request.\n"
                f"Member: {self.member_id}\n"
                f"Tool: {tool_name}\n"
                f"Tool Call ID: {tool_call_id}\n"
                f"Arguments: {args_str}\n"
                "Please review and call approve_tool.\n\n"
            )

            # Send message to leader
            team_logger.info(f"Sending tool approval request to leader for {tool_name} (call_id: {tool_call_id})")
            message_id = await self.message_manager.send_message(
                content=message,
                to_member=self.leader_id
            )

            if not message_id:
                team_logger.error(f"Failed to send approval request for {tool_name}")
                return self.reject(tool_result="Failed to send approval request to leader")

            # Create interrupt request
            request = InterruptRequest(
                message=f"Awaiting leader approval for tool: {tool_name}",
                payload_schema=ConfirmPayload.to_schema(),
                auto_confirm_key=auto_confirm_key,
            )
            return self.interrupt(request)

        # Resume: process leader's approval response
        try:
            if isinstance(user_input, ConfirmPayload):
                payload = user_input
            elif isinstance(user_input, dict):
                payload = ConfirmPayload.model_validate(user_input)
            else:
                # Unknown input format, re-interrupt
                return self.interrupt(InterruptRequest(
                    message=f"Invalid approval response format for tool: {tool_name}",
                    payload_schema=ConfirmPayload.to_schema(),
                    auto_confirm_key=self._get_auto_confirm_key(tool_call),
                ))
        except Exception as e:
            team_logger.error(f"Failed to parse approval response for {tool_name}: {e}")
            return self.interrupt(InterruptRequest(
                message=f"Invalid approval response for tool: {tool_name}",
                payload_schema=ConfirmPayload.to_schema(),
                auto_confirm_key=self._get_auto_confirm_key(tool_call),
            ))

        if payload.approved:
            team_logger.info(f"Tool {tool_name} approved by leader for member {self.member_id}")
            return self.approve()

        feedback = payload.feedback or "Tool call rejected by leader"
        team_logger.info(f"Tool {tool_name} rejected by leader for member {self.member_id}: {feedback}")
        return self.reject(tool_result=feedback)

    @staticmethod
    def _is_auto_confirmed(config: Optional[dict], key: str) -> bool:
        """Check if key is auto-confirmed in config."""
        if config is None:
            return False
        return config.get(key, False)
