# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for event scheduler schema definitions."""

from __future__ import annotations

import pytest

from openjiuwen.extensions.event_scheduler.schema import (
    ScheduleType,
    RetryPolicy,
    EventChainRule,
    ScheduledTaskMixin,
    EventSchedulerConfig,
)


class TestRetryPolicy:
    """Tests for RetryPolicy model."""

    def test_default_values(self):
        """Test default retry policy values."""
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.base_delay == 1.0
        assert policy.max_delay == 60.0
        assert policy.backoff_multiplier == 2.0

    def test_exponential_backoff(self):
        """Test exponential backoff delay calculation."""
        policy = RetryPolicy(base_delay=1.0, backoff_multiplier=2.0, max_delay=60.0)
        assert policy.get_delay(0) == 1.0
        assert policy.get_delay(1) == 2.0
        assert policy.get_delay(2) == 4.0
        assert policy.get_delay(3) == 8.0

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        policy = RetryPolicy(base_delay=10.0, backoff_multiplier=3.0, max_delay=50.0)
        assert policy.get_delay(0) == 10.0
        assert policy.get_delay(1) == 30.0
        assert policy.get_delay(2) == 50.0  # Capped at max_delay
        assert policy.get_delay(3) == 50.0  # Still capped

    def test_base_delay_exceeds_max_delay_raises(self):
        """Test that base_delay > max_delay raises ValueError."""
        with pytest.raises(ValueError, match="base_delay cannot exceed max_delay"):
            RetryPolicy(base_delay=100.0, max_delay=50.0)

    def test_zero_retries(self):
        """Test policy with zero retries."""
        policy = RetryPolicy(max_retries=0)
        assert policy.max_retries == 0


class TestEventChainRule:
    """Tests for EventChainRule model."""

    def test_basic_creation(self):
        """Test basic chain rule creation."""
        rule = EventChainRule(
            rule_id="rule-1",
            source_task_type="extract",
            target_task_type="validate",
        )
        assert rule.rule_id == "rule-1"
        assert rule.source_task_type == "extract"
        assert rule.target_task_type == "validate"
        assert rule.condition is None

    def test_empty_rule_id_raises(self):
        """Test that empty rule_id raises ValueError."""
        with pytest.raises(ValueError):
            EventChainRule(
                rule_id="",
                source_task_type="extract",
                target_task_type="validate",
            )

    def test_evaluate_condition_no_condition(self):
        """Test condition evaluation when no condition is set."""
        rule = EventChainRule(
            rule_id="rule-1",
            source_task_type="extract",
            target_task_type="validate",
        )
        assert rule.evaluate_condition(None) is True
        assert rule.evaluate_condition({"key": "value"}) is True

    def test_evaluate_condition_key_value_match(self):
        """Test condition evaluation with key=value match."""
        rule = EventChainRule(
            rule_id="rule-1",
            source_task_type="extract",
            target_task_type="validate",
            condition="format=csv",
        )
        assert rule.evaluate_condition({"format": "csv"}) is True
        assert rule.evaluate_condition({"format": "json"}) is False
        assert rule.evaluate_condition(None) is False

    def test_evaluate_condition_key_exists(self):
        """Test condition evaluation with key existence check."""
        rule = EventChainRule(
            rule_id="rule-1",
            source_task_type="extract",
            target_task_type="validate",
            condition="validated",
        )
        assert rule.evaluate_condition({"validated": True}) is True
        assert rule.evaluate_condition({"other": True}) is False


class TestScheduledTaskMixin:
    """Tests for ScheduledTaskMixin model."""

    def test_immediate_default(self):
        """Test default schedule type is IMMEDIATE."""
        mixin = ScheduledTaskMixin()
        assert mixin.schedule_type == ScheduleType.IMMEDIATE
        assert mixin.delay_seconds is None
        assert mixin.retry_count == 0

    def test_delayed_requires_delay_seconds(self):
        """Test that DELAYED type requires delay_seconds."""
        with pytest.raises(ValueError, match="delay_seconds is required"):
            ScheduledTaskMixin(schedule_type=ScheduleType.DELAYED)

    def test_delayed_with_delay(self):
        """Test DELAYED type with valid delay_seconds."""
        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=30.0,
        )
        assert mixin.schedule_type == ScheduleType.DELAYED
        assert mixin.delay_seconds == 30.0

    def test_cron_requires_expression(self):
        """Test that CRON type requires cron_expression."""
        with pytest.raises(ValueError, match="cron_expression is required"):
            ScheduledTaskMixin(schedule_type=ScheduleType.CRON)

    def test_cron_with_expression(self):
        """Test CRON type with valid cron_expression."""
        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.CRON,
            cron_expression="0 */5 * * *",
        )
        assert mixin.schedule_type == ScheduleType.CRON
        assert mixin.cron_expression == "0 */5 * * *"

    def test_serialization_roundtrip(self):
        """Test that mixin can be serialized and deserialized."""
        mixin = ScheduledTaskMixin(
            schedule_type=ScheduleType.DELAYED,
            delay_seconds=10.0,
            retry_policy=RetryPolicy(max_retries=5),
            retry_count=2,
        )
        data = mixin.model_dump()
        restored = ScheduledTaskMixin(**data)
        assert restored.schedule_type == mixin.schedule_type
        assert restored.delay_seconds == mixin.delay_seconds
        assert restored.retry_count == mixin.retry_count
        assert restored.retry_policy.max_retries == 5


class TestEventSchedulerConfig:
    """Tests for EventSchedulerConfig model."""

    def test_default_config(self):
        """Test default configuration values."""
        config = EventSchedulerConfig()
        assert config.enable_delayed_scheduling is True
        assert config.enable_event_chaining is True
        assert config.enable_retry is True
        assert config.chain_rules == []
        assert config.default_retry_policy is None
        assert config.scheduler_poll_interval == 0.5

    def test_config_with_rules(self):
        """Test configuration with chain rules."""
        rules = [
            EventChainRule(
                rule_id="r1",
                source_task_type="a",
                target_task_type="b",
            )
        ]
        config = EventSchedulerConfig(chain_rules=rules)
        assert len(config.chain_rules) == 1
        assert config.chain_rules[0].rule_id == "r1"
