# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for SubscriptionManager."""
import pytest

from openjiuwen.core.multi_agent.team_runtime.subscription_manager import SubscriptionManager


class TestSubscriptionManager:
    """Tests for SubscriptionManager."""

    def __init__(self):
        """Initialize test instance attributes."""
        self.manager = None

    def setup_method(self):
        """Create a fresh SubscriptionManager for each test."""
        self.manager = SubscriptionManager()

    # ------------------------------------------------------------------
    # subscribe / unsubscribe
    # ------------------------------------------------------------------

    def test_subscribe_registers_agent_to_topic(self):
        self.manager.subscribe("agent_a", "code_events")
        subscribers = self.manager.get_subscribers("code_events")
        assert "agent_a" in subscribers

    def test_subscribe_multiple_agents_to_same_topic(self):
        self.manager.subscribe("agent_a", "events")
        self.manager.subscribe("agent_b", "events")
        subscribers = self.manager.get_subscribers("events")
        assert set(subscribers) == {"agent_a", "agent_b"}

    def test_subscribe_same_agent_to_multiple_topics(self):
        self.manager.subscribe("agent_a", "topic1")
        self.manager.subscribe("agent_a", "topic2")
        assert "agent_a" in self.manager.get_subscribers("topic1")
        assert "agent_a" in self.manager.get_subscribers("topic2")

    def test_subscribe_idempotent_for_same_agent_topic(self):
        """Subscribing the same agent-topic pair twice should not duplicate."""
        self.manager.subscribe("agent_a", "events")
        self.manager.subscribe("agent_a", "events")
        subscribers = self.manager.get_subscribers("events")
        assert subscribers.count("agent_a") == 1

    def test_unsubscribe_removes_agent_from_topic(self):
        self.manager.subscribe("agent_a", "events")
        self.manager.unsubscribe("agent_a", "events")
        assert "agent_a" not in self.manager.get_subscribers("events")

    def test_unsubscribe_cleans_empty_topic_entry(self):
        """When the last subscriber unsubscribes, topic entry is removed."""
        self.manager.subscribe("agent_a", "events")
        self.manager.unsubscribe("agent_a", "events")
        # Should not appear in subscriptions listing
        result = self.manager.list_subscriptions()
        assert "events" not in result.get("subscriptions", {})

    def test_unsubscribe_nonexistent_agent_is_safe(self):
        """Unsubscribing an agent that never subscribed does not raise."""
        self.manager.unsubscribe("ghost_agent", "no_topic")  # should not raise

    def test_unsubscribe_all_removes_all_subscriptions(self):
        self.manager.subscribe("agent_a", "topic1")
        self.manager.subscribe("agent_a", "topic2")
        self.manager.unsubscribe_all("agent_a")
        assert "agent_a" not in self.manager.get_subscribers("topic1")
        assert "agent_a" not in self.manager.get_subscribers("topic2")

    def test_unsubscribe_all_leaves_other_agents_intact(self):
        self.manager.subscribe("agent_a", "events")
        self.manager.subscribe("agent_b", "events")
        self.manager.unsubscribe_all("agent_a")
        assert "agent_b" in self.manager.get_subscribers("events")
        assert "agent_a" not in self.manager.get_subscribers("events")

    def test_unsubscribe_all_nonexistent_agent_is_safe(self):
        self.manager.unsubscribe_all("ghost")  # should not raise

    # ------------------------------------------------------------------
    # get_subscribers / wildcard matching
    # ------------------------------------------------------------------

    def test_get_subscribers_exact_match(self):
        self.manager.subscribe("agent_a", "code_events")
        result = self.manager.get_subscribers("code_events")
        assert "agent_a" in result

    def test_get_subscribers_no_match_returns_empty(self):
        result = self.manager.get_subscribers("unknown_topic")
        assert result == []

    def test_wildcard_star_matches_any_sequence(self):
        self.manager.subscribe("agent_a", "code_*")
        assert "agent_a" in self.manager.get_subscribers("code_events")
        assert "agent_a" in self.manager.get_subscribers("code_review")
        assert "agent_a" in self.manager.get_subscribers("code_")

    def test_wildcard_star_does_not_match_different_prefix(self):
        self.manager.subscribe("agent_a", "code_*")
        assert "agent_a" not in self.manager.get_subscribers("data_events")

    def test_wildcard_question_mark_matches_single_char(self):
        self.manager.subscribe("agent_a", "event_?")
        assert "agent_a" in self.manager.get_subscribers("event_A")
        assert "agent_a" in self.manager.get_subscribers("event_1")
        assert "agent_a" not in self.manager.get_subscribers("event_AB")  # two chars

    def test_global_wildcard_matches_all(self):
        self.manager.subscribe("agent_a", "*")
        assert "agent_a" in self.manager.get_subscribers("anything")
        assert "agent_a" in self.manager.get_subscribers("code_events")

    def test_multiple_patterns_fan_out(self):
        """Multiple agents with different patterns can all match the same topic."""
        self.manager.subscribe("agent_a", "*")
        self.manager.subscribe("agent_b", "code_*")
        self.manager.subscribe("agent_c", "code_events")
        result = self.manager.get_subscribers("code_events")
        assert set(result) == {"agent_a", "agent_b", "agent_c"}

    # ------------------------------------------------------------------
    # get_subscription_count / list_subscriptions
    # ------------------------------------------------------------------

    def test_get_subscription_count_empty(self):
        assert self.manager.get_subscription_count() == 0

    def test_get_subscription_count_increments(self):
        self.manager.subscribe("agent_a", "t1")
        self.manager.subscribe("agent_b", "t1")
        self.manager.subscribe("agent_a", "t2")
        assert self.manager.get_subscription_count() == 3

    def test_get_subscription_count_decrements_on_unsubscribe(self):
        self.manager.subscribe("agent_a", "t1")
        self.manager.subscribe("agent_a", "t2")
        self.manager.unsubscribe("agent_a", "t1")
        assert self.manager.get_subscription_count() == 1

    def test_list_subscriptions_all(self):
        self.manager.subscribe("agent_a", "t1")
        self.manager.subscribe("agent_b", "t2")
        result = self.manager.list_subscriptions()
        assert "subscriptions" in result
        assert "t1" in result["subscriptions"]
        assert "t2" in result["subscriptions"]

    def test_list_subscriptions_filtered_by_agent(self):
        self.manager.subscribe("agent_a", "t1")
        self.manager.subscribe("agent_a", "t2")
        self.manager.subscribe("agent_b", "t3")
        result = self.manager.list_subscriptions(agent_id="agent_a")
        assert result["agent_id"] == "agent_a"
        topics = result["topics"]
        assert "t1" in topics
        assert "t2" in topics
        assert "t3" not in topics

    def test_list_subscriptions_for_unknown_agent(self):
        result = self.manager.list_subscriptions(agent_id="unknown")
        assert result["topics"] == []
