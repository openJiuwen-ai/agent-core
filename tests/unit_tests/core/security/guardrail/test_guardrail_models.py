# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for guardrail framework data models.
"""

from openjiuwen.core.security.guardrail import (
    GuardrailResult,
    RiskAssessment,
    RiskLevel,
)


class TestGuardrailResult:
    """Tests for GuardrailResult dataclass."""

    @staticmethod
    def test_pass_returns_safe_result():
        """Test GuardrailResult.pass_() returns safe result."""
        result = GuardrailResult.pass_()

        assert result.is_safe is True
        assert result.risk_level == RiskLevel.SAFE
        assert result.risk_type is None
        assert result.details is None
        assert result.modified_data is None

    @staticmethod
    def test_pass_with_details():
        """Test GuardrailResult.pass_() with details."""
        details = {"scan_time": 0.5, "tokens_scanned": 100}
        result = GuardrailResult.pass_(details=details)

        assert result.is_safe is True
        assert result.risk_level == RiskLevel.SAFE
        assert result.details == details

    @staticmethod
    def test_block_returns_unsafe_result():
        """Test GuardrailResult.block() returns blocked result."""
        result = GuardrailResult.block(
            risk_level=RiskLevel.HIGH,
            risk_type="prompt_injection"
        )

        assert result.is_safe is False
        assert result.risk_level == RiskLevel.HIGH
        assert result.risk_type == "prompt_injection"
        assert result.details is None

    @staticmethod
    def test_block_with_details():
        """Test GuardrailResult.block() with details."""
        details = {"matched_pattern": "ignore instructions", "confidence": 0.95}
        result = GuardrailResult.block(
            risk_level=RiskLevel.CRITICAL,
            risk_type="jailbreak_attempt",
            details=details
        )

        assert result.is_safe is False
        assert result.risk_level == RiskLevel.CRITICAL
        assert result.risk_type == "jailbreak_attempt"
        assert result.details == details

    @staticmethod
    def test_block_with_modified_data():
        """Test GuardrailResult.block() with modified_data."""
        modified = {"sanitized_text": "***FILTERED***"}
        result = GuardrailResult.block(
            risk_level=RiskLevel.MEDIUM,
            risk_type="sensitive_data",
            modified_data=modified
        )

        assert result.modified_data == modified

    @staticmethod
    def test_guardrail_result_equality():
        """Test GuardrailResult equality comparison."""
        result1 = GuardrailResult.pass_(details={"key": "value"})
        result2 = GuardrailResult.pass_(details={"key": "value"})
        result3 = GuardrailResult.pass_(details={"key": "other"})

        assert result1 == result2
        assert result1 != result3

    @staticmethod
    def test_guardrail_result_inequality():
        """Test GuardrailResult inequality with different types."""
        result = GuardrailResult.pass_()

        assert result != "not a result"
        assert result is not None


class TestRiskAssessment:
    """Tests for RiskAssessment dataclass."""

    @staticmethod
    def test_safe_assessment():
        """Test RiskAssessment for safe content."""
        assessment = RiskAssessment(
            has_risk=False,
            risk_level=RiskLevel.SAFE
        )

        assert assessment.has_risk is False
        assert assessment.risk_level == RiskLevel.SAFE
        assert assessment.risk_type is None
        assert assessment.confidence == 0.0
        assert assessment.details is None

    @staticmethod
    def test_risky_assessment():
        """Test RiskAssessment for risky content."""
        assessment = RiskAssessment(
            has_risk=True,
            risk_level=RiskLevel.HIGH,
            risk_type="prompt_injection",
            confidence=0.85,
            details={"matched_terms": ["ignore", "system"]}
        )

        assert assessment.has_risk is True
        assert assessment.risk_level == RiskLevel.HIGH
        assert assessment.risk_type == "prompt_injection"
        assert assessment.confidence == 0.85
        assert assessment.details == {"matched_terms": ["ignore", "system"]}

    @staticmethod
    def test_assessment_default_values():
        """Test RiskAssessment default values."""
        assessment = RiskAssessment(
            has_risk=True,
            risk_level=RiskLevel.LOW
        )

        assert assessment.risk_type is None
        assert assessment.confidence == 0.0
        assert assessment.details is None

    @staticmethod
    def test_assessment_equality():
        """Test RiskAssessment equality."""
        assessment1 = RiskAssessment(
            has_risk=True,
            risk_level=RiskLevel.MEDIUM,
            risk_type="test"
        )
        assessment2 = RiskAssessment(
            has_risk=True,
            risk_level=RiskLevel.MEDIUM,
            risk_type="test"
        )
        assessment3 = RiskAssessment(
            has_risk=True,
            risk_level=RiskLevel.MEDIUM,
            risk_type="other"
        )

        assert assessment1 == assessment2
        assert assessment1 != assessment3

    @staticmethod
    def test_assessment_with_all_fields():
        """Test RiskAssessment with all fields populated."""
        details = {"scan_id": "123", "model": "security-v1"}
        assessment = RiskAssessment(
            has_risk=True,
            risk_level=RiskLevel.CRITICAL,
            risk_type="data_leakage",
            confidence=0.99,
            details=details
        )

        assert assessment.has_risk is True
        assert assessment.risk_level == RiskLevel.CRITICAL
        assert assessment.risk_type == "data_leakage"
        assert assessment.confidence == 0.99
        assert assessment.details == details


class TestRiskLevelEnum:
    """Tests for RiskLevel enumeration."""

    @staticmethod
    def test_risk_level_values():
        """Test all RiskLevel enum values exist."""
        assert RiskLevel.SAFE.value == "safe"
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"
        assert RiskLevel.CRITICAL.value == "critical"

    @staticmethod
    def test_risk_level_ordering():
        """Test RiskLevel enum member order."""
        levels = list(RiskLevel)

        assert levels[0] == RiskLevel.SAFE
        assert levels[1] == RiskLevel.LOW
        assert levels[2] == RiskLevel.MEDIUM
        assert levels[3] == RiskLevel.HIGH
        assert levels[4] == RiskLevel.CRITICAL

    @staticmethod
    def test_risk_level_from_string():
        """Test creating RiskLevel from string value."""
        assert RiskLevel("safe") == RiskLevel.SAFE
        assert RiskLevel("high") == RiskLevel.HIGH
        assert RiskLevel("critical") == RiskLevel.CRITICAL

    @staticmethod
    def test_risk_level_contains_all_values():
        """Test RiskLevel enum contains all expected values."""
        values = {level.value for level in RiskLevel}
        assert "safe" in values
        assert "low" in values
        assert "medium" in values
        assert "high" in values
        assert "critical" in values

    @staticmethod
    def test_risk_level_name():
        """Test RiskLevel name property."""
        assert RiskLevel.SAFE.name == "SAFE"
        assert RiskLevel.HIGH.name == "HIGH"
        assert RiskLevel.CRITICAL.name == "CRITICAL"

    @staticmethod
    def test_risk_level_value_property():
        """Test RiskLevel value property."""
        assert RiskLevel.SAFE.value == "safe"
        assert RiskLevel.MEDIUM.value == "medium"
