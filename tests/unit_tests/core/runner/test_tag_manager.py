# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.runner.resources_manager.base import TagUpdateStrategy, TagMatchStrategy
from openjiuwen.core.runner.resources_manager.tag_manager import TagMgr


def test_tag_manager():
    tag_mgr = TagMgr()

    # Step 1: Add tags
    tag_mgr.tag_resource("res-001", ["alpha", "beta"])
    assert set(tag_mgr.get_resources_tags("res-001")) == {"alpha", "beta"}

    # Step 2: Find resources (ANY match)
    tag_mgr.tag_resource("res-002", ["beta", "gamma"])
    result_any = tag_mgr.find_resources_by_tags(["alpha", "gamma"], TagMatchStrategy.ANY)
    assert set(result_any) == {"res-001", "res-002"}
    result_any = tag_mgr.find_resources_by_tags(["delta"], TagMatchStrategy.ANY)
    assert set(result_any) == set()
    result_any = tag_mgr.find_resources_by_tags("beta", TagMatchStrategy.ANY)
    assert set(result_any) == {"res-001", "res-002"}
    with pytest.raises(JiuWenBaseException) as exc_info:
        result_any = tag_mgr.find_resources_by_tags("beta", "merge")

    # Step 3: Find resources (ALL match)
    result_all = tag_mgr.find_resources_by_tags(["beta", "gamma"], TagMatchStrategy.ALL)
    assert result_all == ["res-002"]
    result_all = tag_mgr.find_resources_by_tags(["alpha", "beta"], TagMatchStrategy.ALL)
    assert result_all == ["res-001"]
    result_all = tag_mgr.find_resources_by_tags(["alpha", "beta", "gamma"], TagMatchStrategy.ALL)
    assert result_all == []
    result_all = tag_mgr.find_resources_by_tags("alpha", TagMatchStrategy.ALL)
    assert result_all == ["res-001"]
    with pytest.raises(JiuWenBaseException) as exc_info:
        result_all = tag_mgr.find_resources_by_tags("beta", "test_strategy")

    # Step 4: Merge tags
    tag_mgr.replace_resource_tags("res-001", ["delta"], TagUpdateStrategy.MERGE)
    assert set(tag_mgr.get_resources_tags("res-001")) == {"alpha", "beta", "delta"}
    tag_mgr.replace_resource_tags("res-001", "theta", TagUpdateStrategy.MERGE)
    assert set(tag_mgr.get_resources_tags("res-001")) == {"alpha", "beta", "delta", "theta"}
    tag_mgr.replace_resource_tags("res-002", ["theta", "mu"], TagUpdateStrategy.MERGE)
    assert set(tag_mgr.get_resources_tags("res-002")) == {"gamma", "beta", "mu", "theta"}
    with pytest.raises(JiuWenBaseException) as exc_info:
        result_all = tag_mgr.replace_resource_tags("res-001", "beta", "test_strategy")

    # Step 5: Replace tags
    tag_mgr.replace_resource_tags("res-001", ["omega"], TagUpdateStrategy.REPLACE)
    assert set(tag_mgr.get_resources_tags("res-001")) == {"omega"}

    # Step 6: Remove tags
    tag_mgr.untag_resource("res-001")
    assert tag_mgr.get_resources_tags("res-001") == []
    assert "res-001" not in tag_mgr._resource_tags

    # Step 7: Check if deleted resource still exists (should not)
    result_after_delete = tag_mgr.find_resources_by_tags(["omega"], TagMatchStrategy.ANY)
    assert "res-001" not in result_after_delete
