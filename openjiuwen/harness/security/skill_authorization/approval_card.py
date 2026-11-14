# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill 加载审批卡协议（Web / CLI 对接契约，同版本冻结）。

前端按 ``kind + version`` 识别卡片并渲染，用户回传动作由后端解析；
协议本身属于宿主契约，留在 core，不随权限语义进入 agent_shield。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .models import SkillTrustLevel

#: 审批卡结构化 payload 的协议版本；前端按 ``kind + version`` 识别。
SKILL_APPROVAL_CARD_KIND = "skill_approval"
SKILL_APPROVAL_CARD_VERSION = 1

#: Skill 审批卡的用户响应 schema(user -> backend;三动作协议,前后端同版本冻结)。
#: 无法识别的 action 由后端默认拒绝(fail-closed)。
SKILL_APPROVAL_PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["approve_once", "approve_session", "continue_without_overlay"],
        },
    },
    "required": ["action"],
}

#: ``InterruptRequest.payload_schema`` 中携带结构化审批卡的扩展键。
SKILL_APPROVAL_CARD_EXTENSION_KEY = "x-skill-approval-card"


class SkillApprovalAction(str, Enum):
    APPROVE_ONCE = "approve_once"
    APPROVE_SESSION = "approve_session"
    CONTINUE_WITHOUT_OVERLAY = "continue_without_overlay"


@dataclass(frozen=True)
class SkillPermissionDiff:
    """Skill overlay 相对当前生效配置的权限差分。

    - ``widened``：放宽项（需用户批准才生效），展示在最前；
    - ``tightened``：收紧项（同样需用户批准才生效），展示其次；
    - ``rejected``：试图突破全局 / 父路径 ``deny`` 而被丢弃的声明，单列。
    """

    widened: tuple[str, ...] = ()
    tightened: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "widened": list(self.widened),
            "tightened": list(self.tightened),
            "rejected": list(self.rejected),
        }


@dataclass(frozen=True)
class SkillApprovalCard:
    """Skill 加载审批卡的结构化协议（前端渲染契约）。

    ``to_dict()`` 输出为稳定 JSON 结构；用户回传动作取值见
    ``SkillApprovalAction``（``approve_once`` / ``approve_session`` /
    ``continue_without_overlay``），其中 ``approve_session`` 仅在
    ``trust == builtin`` 时出现在 ``actions`` 中。无法识别的 action
    由后端默认拒绝。
    """

    skill_name: str
    source: str
    version: str | None
    trust: SkillTrustLevel
    permissions_hash: str
    agent_scope_id: str
    diff: SkillPermissionDiff
    actions: tuple[str, ...]
    cached_decision: str | None = None
    kind: str = SKILL_APPROVAL_CARD_KIND
    schema_version: int = SKILL_APPROVAL_CARD_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "schema_version": self.schema_version,
            "skill_name": self.skill_name,
            "source": self.source,
            "version": self.version,
            "trust": self.trust.value,
            "permissions_hash": self.permissions_hash,
            "agent_scope_id": self.agent_scope_id,
            "cached_decision": self.cached_decision,
            "diff": self.diff.to_dict(),
            "actions": list(self.actions),
        }
