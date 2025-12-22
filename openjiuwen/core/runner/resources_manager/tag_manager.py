# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import List

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runner.resources_manager.base import Tag, TagUpdateStrategy, TagMatchStrategy
from openjiuwen.core.runner.resources_manager.thread_safe_dict import ThreadSafeDict


class TagMgr:
    def __init__(self):
        self._resource_tags: ThreadSafeDict[str, set[Tag]] = {}
        self._tag_to_resource: ThreadSafeDict[Tag, set[str]] = {}

    def _normalize_tags(self, tags: list[Tag] | Tag) -> set[Tag]:
        """
        Normalize tags to set

        Args:
            tags (list[Tag] | Tag): A single tag or a list of tags.

        Returns:
            set[Tag]: A set of normalized tags.

        Raises:
            JiuWenBaseException
        """
        if isinstance(tags, str):
            return {tags}
        elif isinstance(tags, List):
            return set(tags)
        else:
            raise JiuWenBaseException(
                StatusCode.SESSION_TAG_MANAGE_FAILED.code,
                StatusCode.SESSION_TAG_MANAGE_FAILED.errmsg.format(
                    reason=f"Invalid tag format, got {type(tags).__name__}"
                )
            )

    def tag_resource(self, resource_id: str, tags: list[Tag] | Tag):
        """
        Tag the resource with resource_id

        Args:
            resource_id (str): The unique identifier of the resource.
            tags (list[Tag] | Tag): Tags to assign to the resource.
        """
        tags_set = self._normalize_tags(tags)
        if resource_id not in self._resource_tags:
            self._resource_tags[resource_id] = set()
        for tag in tags_set:
            if resource_id not in self._resource_tags or tag not in self._resource_tags[resource_id]:
                self._resource_tags[resource_id].add(tag)
            if tag not in self._tag_to_resource or resource_id not in self._tag_to_resource[tag]:
                self._tag_to_resource.setdefault(tag, set()).add(resource_id)

    def untag_resource(self, resource_id: str):
        """
        Untag resource tags with resource_id

        Args:
            resource_id (str): The unique identifier of the resource.
        """
        if resource_id not in self._resource_tags:
            return
        for tag in self._resource_tags[resource_id]:
            self._tag_to_resource[tag].discard(resource_id)
            if not self._tag_to_resource[tag]:
                del self._tag_to_resource[tag]
        del self._resource_tags[resource_id]

    def replace_resource_tags(self, resource_id: str, tags: list[Tag] | Tag, tag_update_strategy: TagUpdateStrategy):
        """
        Replace resource tags according to tag_update_strategy

        Args:
            resource_id (str): The unique identifier of the resource.
            tags (list[Tag] | Tag): New tags to apply.
            tag_update_strategy (TagUpdateStrategy): Strategy to apply (REPLACE or MERGE).

        Raises:
            JiuWenBaseException
        """
        new_tags = self._normalize_tags(tags)
        current_tags = self._resource_tags.get(resource_id, set())

        if tag_update_strategy == TagUpdateStrategy.REPLACE:
            self.untag_resource(resource_id)
            self._resource_tags[resource_id] = set()
        elif tag_update_strategy == TagUpdateStrategy.MERGE:
            new_tags |= current_tags
        else:
            raise JiuWenBaseException(
                StatusCode.SESSION_TAG_MANAGE_FAILED.code,
                StatusCode.SESSION_TAG_MANAGE_FAILED.errmsg.format(
                    reason=f"Invalid tag update strategy, got {str(tag_update_strategy)}"
                )
            )

        self.tag_resource(resource_id, list(new_tags))

    def find_resources_by_tags(self, tags: list[Tag] | Tag, tag_match_strategy: TagMatchStrategy) -> list[str]:
        """
        Find resources by tags according to tag_match_strategy

        Args:
            tags (list[Tag] | Tag): Tags to search for.
            tag_match_strategy (TagMatchStrategy): Strategy to match tags (ANY or ALL).

        Returns:
            list[str]

        Raises:
            JiuWenBaseException
        """
        tags_set = self._normalize_tags(tags)
        if not tags_set:
            return []

        if tag_match_strategy == TagMatchStrategy.ANY:
            result = set()
            for tag in tags_set:
                result |= self._tag_to_resource.get(tag, set())
            return list(result)

        elif tag_match_strategy == TagMatchStrategy.ALL:
            result = None
            for tag in tags_set:
                resources = self._tag_to_resource.get(tag, set())
                if result is None:
                    result = resources.copy()
                else:
                    result &= resources
            return list(result or [])

        else:
            raise JiuWenBaseException(
                StatusCode.SESSION_TAG_MANAGE_FAILED.code,
                StatusCode.SESSION_TAG_MANAGE_FAILED.errmsg.format(
                    reason=f"Invalid tag match strategy, got {str(tag_match_strategy)}"
                )
            )

    def get_resources_tags(self, resource_id: str) -> list[Tag]:
        """
        Get resource tags by resource_id

        Args:
            resource_id (str): The unique identifier of the resource.

        Returns:
            list[Tag]: List of tags associated with the resource.
        """
        return list(self._resource_tags.get(resource_id, set()))
