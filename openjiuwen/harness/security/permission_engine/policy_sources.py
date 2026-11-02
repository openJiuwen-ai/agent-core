# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Policy source identifiers and the shared composition source schema."""

from typing import Any

SOURCE_USER = "user"
SOURCE_CLOUD = "cloud"
SOURCE_BUILTIN = "builtin"
SOURCE_USER_GROUP = "user_group"
SOURCE_CLOUD_GROUP = "cloud_group"
SOURCE_BUILTIN_GROUP = "builtin_group"

# Keep this default for hosts that only have three source readers.
DEFAULT_SOURCE_ORDER = (SOURCE_USER, SOURCE_CLOUD, SOURCE_BUILTIN)
GROUP_SOURCE_BY_OWNER = {
    SOURCE_USER: SOURCE_USER_GROUP,
    SOURCE_CLOUD: SOURCE_CLOUD_GROUP,
    SOURCE_BUILTIN: SOURCE_BUILTIN_GROUP,
}
DEFAULT_LAYER_ORDER = tuple(layer for owner in DEFAULT_SOURCE_ORDER for layer in (owner, GROUP_SOURCE_BY_OWNER[owner]))
POLICY_SOURCE_IDS = frozenset(DEFAULT_LAYER_ORDER)


def validate_source_order(order: Any, *, schema_version: int = 2) -> list[str]:
    """Validate the enabled, priority-ordered source IDs in a snapshot."""
    # bool is an int subclass but is never a schema version.
    if type(schema_version) is not int or schema_version not in (1, 2):  # pylint: disable=unidiomatic-typecheck
        raise ValueError("unsupported permissions.composition schema_version")
    allowed = frozenset(DEFAULT_SOURCE_ORDER) if schema_version == 1 else POLICY_SOURCE_IDS
    if not isinstance(order, list) or any(
        not isinstance(source, str) or source not in allowed for source in order
    ) or len(order) != len(set(order)):
        raise ValueError("invalid permissions.composition source_order")
    if any(group in order and owner not in order for owner, group in GROUP_SOURCE_BY_OWNER.items()):
        raise ValueError("permissions.composition group layer requires its owner")
    return order


def validate_policy_sources(sources: Any) -> list[dict[str, Any]]:
    """Validate host settings without deciding which sources are enabled."""
    if not isinstance(sources, list):
        raise ValueError("policy_sources must be a list")
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"id", "enabled"}:
            raise ValueError("each policy source requires id and enabled")
        source_id = source["id"]
        if not isinstance(source_id, str) or source_id not in POLICY_SOURCE_IDS or source_id in seen:
            raise ValueError("unknown or duplicate policy source")
        if not isinstance(source["enabled"], bool):
            raise ValueError("policy source enabled must be boolean")
        seen.add(source_id)
    return sources
