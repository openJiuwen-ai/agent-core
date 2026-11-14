# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""权限配置热更新时的动态授权联动。

配置热更新属于 harness 生命周期：关闭动态授权时要同时撤销 Grant、推进撤销代次，
并清空等待中的委托审批。权限判定语义仍由本包 :mod:`.schema` / :mod:`.grant_store` 提供。
"""

from __future__ import annotations

import logging
from typing import Any

from .grant_store import reset_all_grants
from .schema import is_skill_authorization_enabled
from .subagent_approval_registry import get_subagent_approval_registry

logger = logging.getLogger(__name__)


def sync_grants_on_permissions_reload(
    old_config: Any,
    new_config: Any,
) -> None:
    """权限配置热更新联动。

    - ``skill_authorization.enabled`` 由 true 变 false：清空全部 Grant；
    - 由 false 变 true：不自动激活已加载 Skill（仅记日志，须重新调用 ``skill_tool``）；
    - 其余普通热更新：不清 Grant。
    """
    was_enabled = is_skill_authorization_enabled(old_config)
    now_enabled = is_skill_authorization_enabled(new_config)
    if was_enabled and not now_enabled:
        reset_all_grants()
        get_subagent_approval_registry().clear_all()
        logger.info(
            "[skill_authorization] feature disabled at runtime; all grants cleared",
        )
    elif now_enabled and not was_enabled:
        logger.info(
            "[skill_authorization] feature enabled at runtime; "
            "already-loaded skills are NOT auto-activated (reload via skill_tool required)",
        )
