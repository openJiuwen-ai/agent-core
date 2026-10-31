"""Platform-neutral source identifiers survive the search protocol and presentation."""

import pytest
from pydantic import ValidationError

from openjiuwen.harness.persistent_agent import GroupMessageQuery, GroupMessageSearchHit, GroupMessageSearchResult
from openjiuwen.harness.persistent_agent.tools.group_history import _presentation
from tests.unit_tests.harness.persistent_agent.test_group_history import execution, hit, response


def test_platform_is_an_optional_bounded_query_scope():
    assert GroupMessageQuery(query="plan").platforms == ()
    assert GroupMessageQuery(platforms=("feishu",)).platforms == ("feishu",)
    with pytest.raises(ValidationError):
        GroupMessageQuery(query="plan", platforms=("../secret",))


def test_message_results_preserve_platform_even_for_same_external_id(execution):
    hits = tuple(GroupMessageSearchHit(**{
        **hit(group="group-" + platform), "platform": platform, "platform_chat_id": "same-id",
    }) for platform in ("feishu", "dingtalk"))
    result = GroupMessageSearchResult(**response(execution, status="found", hits=hits, query_complete=True))
    shown = _presentation(result)
    assert [h["platform"] for h in shown["hits"]] == ["feishu", "dingtalk"]
    assert GroupMessageSearchHit(**hit()).platform is None
