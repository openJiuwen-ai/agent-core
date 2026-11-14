# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""技能级动态授权数据模型：SkillManifest / SkillGrant 及相关枚举。

审批卡协议符号（``SkillApprovalCard`` 等）由 :mod:`.approval_card` 提供；
本模块按历史导出路径惰性转出，避免与 ``approval_card`` 形成循环导入。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillTrustLevel(str, Enum):
    """Skill 可信标记（v1 二值；``approve_session`` 仅对 ``BUILTIN`` 开放）。"""

    BUILTIN = "builtin"
    OTHER = "other"


class GrantStatus(str, Enum):
    """Grant 状态机。

    - ``PENDING_ACTIVATION``：用户已批准，等待正文加载成功（不产生权限）。
    - ``ACTIVE``：正文加载成功且身份匹配（参与权限合成）。
    - ``APPROVED_INACTIVE``：仅保留会话内审批缓存（不产生权限）。
    """

    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    ACTIVE = "ACTIVE"
    APPROVED_INACTIVE = "APPROVED_INACTIVE"


class GrantDecision(str, Enum):
    """授权范围。

    - ``LOCAL``：本次允许，仅当前 Skill 激活期有效，``skill_complete`` 即回收。
    - ``SESSION``：会话内允许，Skill 结束后保留审批记录（``APPROVED_INACTIVE``）。
    """

    LOCAL = "local"
    SESSION = "session"


@dataclass(frozen=True)
class SkillManifest:
    """Skill 身份记录（``skill_permissions.json`` 校验产物）。

    身份五元组为 ``skill_name + source + version + permissions_hash + skill_md_hash``。

    - ``authorizable=True``：校验通过，可进入审批 / 授权流程。
    - ``authorizable=False``：``skill_permissions.json`` 非法或解析失败，
      ``errors`` 携带失败原因；overlay 视为空，Skill 本体按原有权限运行（fail-closed）。
    """

    skill_name: str
    source: str
    version: str | None
    trust: SkillTrustLevel
    permissions_hash: str
    skill_md_hash: str = ""
    authorizable: bool = True
    errors: tuple[str, ...] = ()
    risk_level: str | None = None
    risk_status: str = "missing"
    overlay: dict[str, Any] = field(default_factory=dict, compare=False)

    def identity_tuple(self) -> tuple[str, str, str | None, str, str]:
        return (
            self.skill_name,
            self.source,
            self.version,
            self.permissions_hash,
            self.skill_md_hash,
        )


@dataclass
class SkillGrant:
    """一份 Skill 授权记录。

    绑定 Manifest 身份五元组，只保存用户批准的 Skill overlay 快照
    （``overlay_snapshot``），不保存权限合成结果；激活前必须重新核验
    Manifest 身份与摘要，任一不一致则不激活。
    """

    skill_name: str
    source: str
    version: str | None
    permissions_hash: str
    skill_md_hash: str
    overlay_snapshot: dict[str, Any]
    decision: GrantDecision
    status: GrantStatus
    approval_tool_call_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def identity_tuple(self) -> tuple[str, str, str | None, str, str]:
        return (
            self.skill_name,
            self.source,
            self.version,
            self.permissions_hash,
            self.skill_md_hash,
        )

    def matches_identity(self, manifest: SkillManifest) -> bool:
        return self.identity_tuple() == manifest.identity_tuple()

    def touch(self, status: GrantStatus | None = None) -> None:
        if status is not None:
            self.status = status
        self.updated_at = time.time()


#: 审批卡协议符号：历史上由本模块转出，保持导入路径可用。
_APPROVAL_CARD_EXPORTS = frozenset(
    {
        "SKILL_APPROVAL_CARD_KIND",
        "SKILL_APPROVAL_CARD_VERSION",
        "SkillApprovalAction",
        "SkillApprovalCard",
        "SkillPermissionDiff",
    }
)


def __getattr__(name: str) -> Any:
    if name in _APPROVAL_CARD_EXPORTS:
        from . import approval_card

        return getattr(approval_card, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "GrantDecision",
    "GrantStatus",
    "SkillGrant",
    "SkillManifest",
    "SkillTrustLevel",
]
