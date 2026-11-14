# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""技能级动态授权：以 Skill 生命周期为作用域的临时权限授权。

权限语义（声明校验、权限合成、Grant 记录）实现在本包内；
harness 侧负责 Skill 目录约定、审批卡协议与委托审批路由，并在此统一对外导出。
"""

from .approval_card import (
    SKILL_APPROVAL_CARD_EXTENSION_KEY,
    SKILL_APPROVAL_CARD_KIND,
    SKILL_APPROVAL_CARD_VERSION,
    SKILL_APPROVAL_PAYLOAD_SCHEMA,
    SkillApprovalAction,
    SkillApprovalCard,
    SkillPermissionDiff,
)
from .composer import (
    SkillAuthorizationContext,
    command_rule_fingerprint,
    compose_skill_permissions,
    effective_file_guard_axis_level,
    get_skill_authorization_context,
    reset_skill_authorization_context,
    setup_skill_authorization_context,
)
from .grant_store import (
    SkillGrantStore,
    get_skill_authorization_generation,
    get_skill_grant_store,
    set_skill_grant_store,
)
from .manifest import (
    load_skill_manifest,
)
from .models import (
    GrantDecision,
    GrantStatus,
    SkillGrant,
    SkillManifest,
    SkillTrustLevel,
)
from .reload import (
    sync_grants_on_permissions_reload,
)
from .schema import (
    SKILL_AUTHORIZATION_ENABLED_ENV,
    SKILL_PERMISSION_FILENAME,
    SkillPermissionValidationError,
    canonical_skill_permission_json,
    compute_permissions_hash,
    compute_skill_md_hash,
    is_skill_authorization_enabled,
    normalize_skill_permission,
    normalize_skill_risk,
    validate_skill_permission,
)
from .subagent_approval_registry import (
    SubagentApprovalKind,
    SubagentApprovalRegistry,
    SubagentApprovalRequest,
    SubagentApprovalTimeout,
    get_subagent_approval_registry,
)

__all__ = [
    # Models
    "GrantDecision",
    "GrantStatus",
    "SkillApprovalAction",
    "SkillGrant",
    "SkillManifest",
    "SkillTrustLevel",
    # Approval protocol (Web / CLI 契约)
    "SKILL_APPROVAL_CARD_KIND",
    "SKILL_APPROVAL_CARD_VERSION",
    "SkillApprovalCard",
    "SkillPermissionDiff",
    # Schema
    "SKILL_PERMISSION_FILENAME",
    "SKILL_AUTHORIZATION_ENABLED_ENV",
    "SkillPermissionValidationError",
    "canonical_skill_permission_json",
    "compute_permissions_hash",
    "compute_skill_md_hash",
    "normalize_skill_risk",
    "normalize_skill_permission",
    "validate_skill_permission",
    # Feature flag + 审批响应协议
    "is_skill_authorization_enabled",
    "SKILL_APPROVAL_PAYLOAD_SCHEMA",
    "SKILL_APPROVAL_CARD_EXTENSION_KEY",
    # Direct-subagent delegated approval
    "SubagentApprovalKind",
    "SubagentApprovalRegistry",
    "SubagentApprovalRequest",
    "SubagentApprovalTimeout",
    "get_subagent_approval_registry",
    # Manifest
    "load_skill_manifest",
    # Grant store
    "SkillGrantStore",
    "get_skill_authorization_generation",
    "get_skill_grant_store",
    "set_skill_grant_store",
    "sync_grants_on_permissions_reload",
    # Composer + request context
    "SkillAuthorizationContext",
    "command_rule_fingerprint",
    "compose_skill_permissions",
    "effective_file_guard_axis_level",
    "get_skill_authorization_context",
    "reset_skill_authorization_context",
    "setup_skill_authorization_context",
]
