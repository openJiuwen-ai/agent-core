# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for extraction_models"""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from openjiuwen.core.memory.graph.extraction.base import MULTILINGUAL_DESCRIPTION
from openjiuwen.core.memory.graph.extraction.extraction_models import (
    Datetime,
    Duplication,
    EntityDeclaration,
    EntityDuplication,
    EntityExtraction,
    EntitySummary,
    Fact,
    MergeRelations,
    PossibleTimezone,
    RelationExtraction,
    RelevantFacts,
    TimezonePredictions,
)


@pytest.fixture(autouse=True)
def patch_multilingual_description():
    """Ensure MULTILINGUAL_DESCRIPTION has cn/en for schema methods"""
    with patch.dict(MULTILINGUAL_DESCRIPTION, {"cn": {}, "en": {}}, clear=False):
        yield


class TestDatetime:
    """Tests for Datetime model"""

    @staticmethod
    def test_valid_datetime():
        """Valid fields create Datetime instance"""
        d = Datetime(year=2025, month=3, day=18, hour=12, minute=30, second=0)
        assert d.year == 2025
        assert d.month == 3
        assert d.day == 18


class TestEntityDeclaration:
    """Tests for EntityDeclaration"""

    @staticmethod
    def test_entity_declaration_required_fields():
        """name and entity_type_id are required"""
        e = EntityDeclaration(name="Alice", entity_type_id=0)
        assert e.name == "Alice"
        assert e.entity_type_id == 0

    @staticmethod
    def test_entity_declaration_missing_name_raises():
        """Missing name raises ValidationError"""
        with pytest.raises(ValidationError):
            EntityDeclaration(entity_type_id=0)


class TestDuplication:
    """Tests for Duplication"""

    @staticmethod
    def test_duplication_fields():
        """Duplication has name, id, duplicate_ids"""
        d = Duplication(name="X", id=1, duplicate_ids=[2, 3])
        assert d.name == "X"
        assert d.id == 1
        assert d.duplicate_ids == [2, 3]


class TestFact:
    """Tests for Fact"""

    @staticmethod
    def test_fact_all_fields():
        """Fact has name, fact, valid_since, valid_until, source_id, target_id"""
        f = Fact(
            name="knows",
            fact="works with",
            valid_since="2020-01-01",
            valid_until="2025-01-01",
            source_id=1,
            target_id=2,
        )
        assert f.name == "knows"
        assert f.source_id == 1
        assert f.target_id == 2


class TestEntityExtraction:
    """Tests for EntityExtraction"""

    @staticmethod
    def test_entity_extraction_list():
        """extracted_entities is list of EntityDeclaration"""
        e1 = EntityDeclaration(name="A", entity_type_id=0)
        model = EntityExtraction(extracted_entities=[e1])
        assert len(model.extracted_entities) == 1
        assert model.extracted_entities[0].name == "A"


class TestEntitySummary:
    """Tests for EntitySummary"""

    @staticmethod
    def test_entity_summary_summary_and_attributes():
        """summary and attributes are set"""
        model = EntitySummary(summary="A person.", attributes={"role": "user"})
        assert model.summary == "A person."
        assert model.attributes == {"role": "user"}


class TestEntityDuplication:
    """Tests for EntityDuplication"""

    @staticmethod
    def test_entity_duplication_duplicated_entities():
        """duplicated_entities is list of Duplication"""
        d = Duplication(name="X", id=1, duplicate_ids=[2])
        model = EntityDuplication(duplicated_entities=[d])
        assert len(model.duplicated_entities) == 1
        assert model.duplicated_entities[0].name == "X"


class TestRelationExtraction:
    """Tests for RelationExtraction"""

    @staticmethod
    def test_relation_extraction_extracted_relations():
        """extracted_relations is list of Fact"""
        f = Fact(name="r", fact="f", valid_since="", valid_until="", source_id=1, target_id=2)
        model = RelationExtraction(extracted_relations=[f])
        assert len(model.extracted_relations) == 1


class TestRelevantFacts:
    """Tests for RelevantFacts"""

    @staticmethod
    def test_relevant_facts_reasoning_and_list():
        """brief_reasoning and relevant_relations are set"""
        model = RelevantFacts(brief_reasoning="Because.", relevant_relations=[1, 2])
        assert model.brief_reasoning == "Because."
        assert model.relevant_relations == [1, 2]


class TestTimezonePredictions:
    """Tests for TimezonePredictions"""

    @staticmethod
    def test_timezone_predictions_list():
        """extracted_relations is list of PossibleTimezone"""
        t = PossibleTimezone(name="UTC", offset_from_utc="+0", reasoning="default")
        model = TimezonePredictions(extracted_relations=[t])
        assert len(model.extracted_relations) == 1
        assert model.extracted_relations[0].name == "UTC"


class TestMergeRelations:
    """Tests for MergeRelations"""

    @staticmethod
    def test_merge_relations_all_fields():
        """
        MergeRelations fields need_merging, short_reasoning, combined_content, duplicate_ids, valid_since, valid_until
        are set
        """
        model = MergeRelations(
            need_merging=True,
            short_reasoning="Same event",
            combined_content="Merged.",
            duplicate_ids=[1, 2],
            valid_since="2020-01-01",
            valid_until="2025-01-01",
        )
        assert model.need_merging is True
        assert model.duplicate_ids == [1, 2]
