# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from openjiuwen.core.runner.resources_manager.base import GLOBAL, TagUpdateStrategy, TagMatchStrategy
from openjiuwen.core.runner.resources_manager.tag_manager import TagMgr


class TestTagMgr:
    """TagMgr 测试类"""
    def setup_method(self):
        """每个测试方法前的初始化"""
        self._tag_mgr = TagMgr()
        # 初始化一些测试资源
        self._tag_mgr._resource_tags = {
            "res1": {"tag1", "tag2"},
            "res2": {"tag2", "tag3"},
            "res3": {GLOBAL},
            "res4": {"tag1", "tag3", "tag4"}
        }
        self._tag_mgr._tag_to_resource = {
            "tag1": {"res1", "res4"},
            "tag2": {"res1", "res2"},
            "tag3": {"res2", "res4"},
            "tag4": {"res4"},
            GLOBAL: {"res3"}
        }

    def test_has_tag(self):
        """测试 has_tag 方法"""
        assert self._tag_mgr.has_tag("tag1") is True
        assert self._tag_mgr.has_tag("tag5") is False
        assert self._tag_mgr.has_tag(GLOBAL) is True

    def test_list_tags(self):
        """测试 list_tags 方法"""
        tags = self._tag_mgr.list_tags()
        assert "tag1" in tags
        assert "tag2" in tags
        assert "tag3" in tags
        assert "tag4" in tags
        assert GLOBAL in tags  # GLOBAL 包含在返回列表中
        assert len(tags) == 5

    def test_has_resource(self):
        """测试 has_resource 方法"""
        assert self._tag_mgr.has_resource("res1") is True
        assert self._tag_mgr.has_resource("res5") is False

    def test_tag_resource_normal(self):
        """测试正常添加标签"""
        # 为 res1 添加新标签
        current_tags = self._tag_mgr.tag_resource("res1", ["tag5", "tag6"])
        assert "tag5" in current_tags
        assert "tag6" in current_tags
        assert "tag1" in current_tags
        assert "tag2" in current_tags

        # 验证标签到资源的映射
        assert "res1" in self._tag_mgr._tag_to_resource["tag5"]
        assert "res1" in self._tag_mgr._tag_to_resource["tag6"]

    def test_tag_resource_with_global(self):
        """测试添加 GLOBAL 标签"""
        current_tags = self._tag_mgr.tag_resource("res1", GLOBAL)
        assert current_tags == [GLOBAL]
        assert self._tag_mgr._resource_tags["res1"] == {GLOBAL}
        assert "res1" in self._tag_mgr._tag_to_resource[GLOBAL]


    def test_remove_resource(self):
        """测试删除资源"""
        removed_tags = self._tag_mgr.remove_resource("res1")
        assert set(removed_tags) == {"tag1", "tag2"}
        assert not self._tag_mgr.has_resource("res1")

        # 验证标签映射也被清理
        assert "res1" not in self._tag_mgr._tag_to_resource["tag1"]
        assert "res1" not in self._tag_mgr._tag_to_resource["tag2"]

    def test_remove_nonexistent_resource(self):
        """测试删除不存在的资源"""
        result = self._tag_mgr.remove_resource("res99")
        assert result == []

    def test_remove_resource_tags(self):
        """测试删除指定标签"""
        remaining_tags = self._tag_mgr.remove_resource_tags("res1", ["tag1", "tag3"], skip_if_not_exists=True)
        assert "tag1" not in remaining_tags
        assert "tag2" in remaining_tags

        # 验证标签映射被清理
        assert "res1" not in self._tag_mgr._tag_to_resource["tag1"]

    def test_update_resource_tags_replace(self):
        """测试替换标签策略"""
        new_tags = ["tag5", "tag6"]
        current_tags = self._tag_mgr.update_resource_tags(
            "res1", new_tags, TagUpdateStrategy.REPLACE
        )
        assert set(current_tags) == set(new_tags)
        assert "tag1" not in current_tags
        assert "tag2" not in current_tags

        # 验证旧标签映射被清理
        assert "res1" not in self._tag_mgr._tag_to_resource["tag1"]
        assert "res1" not in self._tag_mgr._tag_to_resource["tag2"]
        # 验证新标签映射被添加
        assert "res1" in self._tag_mgr._tag_to_resource["tag5"]
        assert "res1" in self._tag_mgr._tag_to_resource["tag6"]

    def test_update_resource_tags_merge(self):
        """测试合并标签策略"""
        new_tags = ["tag5", "tag6"]
        current_tags = self._tag_mgr.update_resource_tags(
            "res1", new_tags, TagUpdateStrategy.MERGE
        )
        assert "tag1" in current_tags
        assert "tag2" in current_tags
        assert "tag5" in current_tags
        assert "tag6" in current_tags

    def test_update_to_global(self):
        """测试更新为 GLOBAL 标签"""
        old_tags = self._tag_mgr.update_resource_tags(
            "res1", GLOBAL, TagUpdateStrategy.REPLACE
        )
        assert old_tags == [GLOBAL]
        assert self._tag_mgr._resource_tags["res1"] == {GLOBAL}
        assert "res1" in self._tag_mgr._tag_to_resource[GLOBAL]

    def test_remove_tag(self):
        """测试删除标签"""
        affected_resources = self._tag_mgr.remove_tag("tag1")
        assert "res1" in affected_resources
        assert "res4" in affected_resources
        assert not self._tag_mgr.has_tag("tag1")

        # 验证资源标签被清理
        assert "tag1" not in self._tag_mgr._resource_tags["res1"]
        assert "tag1" not in self._tag_mgr._resource_tags["res4"]

    def test_get_tag_resources(self):
        """测试获取标签对应的资源"""
        resources = self._tag_mgr.get_tag_resources("tag1")
        assert "res1" in resources
        assert "res4" in resources
        assert len(resources) == 2

    def test_find_resources_by_tags_any(self):
        """测试 ANY 匹配策略"""
        resources = self._tag_mgr.find_resources_by_tags(
            ["tag1", "tag3"], TagMatchStrategy.ANY
        )
        assert "res1" in resources  # 有 tag1
        assert "res2" in resources  # 有 tag3
        assert "res4" in resources  # 有 tag1 和 tag3
        assert "res3" not in resources  # GLOBAL 不包含

    def test_find_resources_by_tags_all(self):
        """测试 ALL 匹配策略"""
        resources = self._tag_mgr.find_resources_by_tags(
            ["tag1", "tag3"], TagMatchStrategy.ALL
        )
        assert "res1" not in resources  # 只有 tag1，没有 tag3
        assert "res2" not in resources  # 只有 tag3，没有 tag1
        assert "res4" in resources  # 既有 tag1 又有 tag3
        assert "res3" not in resources  # GLOBAL 不包含

    def test_find_resources_with_nonexistent_tag(self):
        """测试查找不存在的标签"""
        with pytest.raises(Exception) as exc_info:
            self._tag_mgr.find_resources_by_tags(
                ["tag99"], TagMatchStrategy.ANY, skip_if_not_exists=False
            )
        assert "does not exist" in str(exc_info.value)

    def test_find_resources_skip_nonexistent_tag(self):
        """测试跳过不存在的标签"""
        resources = self._tag_mgr.find_resources_by_tags(
            ["tag1", "tag99"], TagMatchStrategy.ANY, skip_if_not_exists=True
        )
        assert "res1" in resources
        assert "res4" in resources

    def test_has_resource_tag(self):
        """测试检查资源是否有指定标签"""
        assert self._tag_mgr.has_resource_tag("res1", "tag1") is True
        assert self._tag_mgr.has_resource_tag("res1", "tag3") is False
        assert self._tag_mgr.has_resource_tag("res3", GLOBAL) is True

    def test_get_resources_tags(self):
        """测试获取资源的标签"""
        tags = self._tag_mgr.get_resources_tags("res1")
        assert set(tags) == {"tag1", "tag2"}

    def test_display(self):
        """测试显示状态"""
        result = self._tag_mgr.display(enable_log=False)
        assert "Tag -> Resource IDs:" in result
        assert "Resource -> Tags:" in result
        assert "Statistics:" in result

    def test_concurrent_operations(self):
        """简单测试并发操作"""
        import threading

        def add_tags(resource_id, tags):
            try:
                self._tag_mgr.tag_resource(resource_id, tags)
            except Exception:
                pass

        threads = []
        for i in range(5):
            t = threading.Thread(target=add_tags, args=(f"res{i}", [f"tag{i}"]))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

    def test_normalize_tags(self):
        """测试标签标准化"""
        # 测试单个标签
        result = self._tag_mgr._normalize_tags("tag1")
        assert result == {"tag1"}

        # 测试标签列表
        result = self._tag_mgr._normalize_tags(["tag1", "tag2"])
        assert result == {"tag1", "tag2"}

    def test_is_builtin_tag(self):
        """测试内置标签检查"""
        assert self._tag_mgr._is_builtin_tag(GLOBAL) is True
        assert self._tag_mgr._is_builtin_tag("tag1") is False