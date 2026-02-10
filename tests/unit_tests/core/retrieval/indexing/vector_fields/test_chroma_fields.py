# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Chroma vector fields test cases
"""

import pytest
from pydantic import ValidationError

from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField


class TestChromaVectorField:
    """Test cases for ChromaVectorField"""

    @staticmethod
    def test_init_default():
        """Test initialization with default values"""
        field = ChromaVectorField()
        assert field.vector_field == "embedding"
        assert field.database_type == "chroma"
        assert field.index_type == "hnsw"
        assert field.max_neighbors == 16
        assert field.ef_construction == 100
        assert field.ef_search == 100
        assert field.extra_search == {}

    @staticmethod
    def test_init_custom_vector_field():
        """Test initialization with custom vector field name"""
        field = ChromaVectorField(vector_field="custom_embedding")
        assert field.vector_field == "custom_embedding"
        assert field.database_type == "chroma"
        assert field.index_type == "hnsw"

    @staticmethod
    def test_init_custom_parameters():
        """Test initialization with custom parameters"""
        field = ChromaVectorField(
            vector_field="embeddings",
            max_neighbors=32,
            ef_construction=200,
            ef_search=150.5,
        )
        assert field.vector_field == "embeddings"
        assert field.max_neighbors == 32
        assert field.ef_construction == 200
        assert field.ef_search == 150.5

    @staticmethod
    def test_init_max_neighbors_min():
        """Test initialization with minimum max_neighbors"""
        field = ChromaVectorField(max_neighbors=2)
        assert field.max_neighbors == 2

    @staticmethod
    def test_init_max_neighbors_max():
        """Test initialization with maximum max_neighbors"""
        field = ChromaVectorField(max_neighbors=2048)
        assert field.max_neighbors == 2048

    @staticmethod
    def test_init_ef_construction_min():
        """Test initialization with minimum ef_construction"""
        field = ChromaVectorField(ef_construction=1)
        assert field.ef_construction == 1

    @staticmethod
    def test_init_ef_search_min():
        """Test initialization with minimum ef_search"""
        field = ChromaVectorField(ef_search=1)
        assert field.ef_search == 1

    @staticmethod
    def test_init_ef_search_float():
        """Test initialization with float ef_search"""
        field = ChromaVectorField(ef_search=50.5)
        assert field.ef_search == 50.5

    @staticmethod
    def test_init_extra_search_empty():
        """Test initialization with empty extra_search"""
        field = ChromaVectorField(extra_search={})
        assert field.extra_search == {}

    @staticmethod
    def test_init_extra_search_valid():
        """Test initialization with valid extra_search parameters"""
        field = ChromaVectorField(
            extra_search={
                "resize_factor": 2.0,
                "num_threads": 4,
                "batch_size": 100,
                "sync_threshold": 10,
            }
        )
        assert field.extra_search["resize_factor"] == 2.0
        assert field.extra_search["num_threads"] == 4
        assert field.extra_search["batch_size"] == 100
        assert field.extra_search["sync_threshold"] == 10

    @staticmethod
    def test_init_extra_search_partial():
        """Test initialization with partial extra_search parameters"""
        field = ChromaVectorField(extra_search={"num_threads": 8})
        assert field.extra_search["num_threads"] == 8

    @staticmethod
    def test_validation_max_neighbors_too_low():
        """Test validation error for max_neighbors below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            ChromaVectorField(max_neighbors=1)
        errors = exc_info.value.errors()
        assert any(error["type"] == "greater_than_equal" and "max_neighbors" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_max_neighbors_too_high():
        """Test validation error for max_neighbors above maximum"""
        with pytest.raises(ValidationError) as exc_info:
            ChromaVectorField(max_neighbors=2049)
        errors = exc_info.value.errors()
        assert any(error["type"] == "less_than_equal" and "max_neighbors" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_ef_construction_too_low():
        """Test validation error for ef_construction below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            ChromaVectorField(ef_construction=0)
        errors = exc_info.value.errors()
        assert any(error["type"] == "greater_than_equal" and "ef_construction" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_ef_search_too_low():
        """Test validation error for ef_search below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            ChromaVectorField(ef_search=0.5)
        errors = exc_info.value.errors()
        assert any(error["type"] == "greater_than_equal" and "ef_search" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_extra_search_invalid_resize_factor():
        """Test validation error for invalid resize_factor type"""
        with pytest.raises(ValidationError) as exc_info:
            ChromaVectorField(extra_search={"resize_factor": "invalid"})
        errors = exc_info.value.errors()
        assert any("invalid_resize_factor" in str(error) for error in errors)

    @staticmethod
    def test_validation_extra_search_invalid_num_threads():
        """Test validation error for invalid num_threads type"""
        with pytest.raises(ValidationError) as exc_info:
            ChromaVectorField(extra_search={"num_threads": "invalid"})
        errors = exc_info.value.errors()
        assert any("invalid_num_threads" in str(error) for error in errors)

    @staticmethod
    def test_validation_extra_search_invalid_batch_size():
        """Test validation error for invalid batch_size type"""
        with pytest.raises(ValidationError) as exc_info:
            ChromaVectorField(extra_search={"batch_size": "invalid"})
        errors = exc_info.value.errors()
        assert any("invalid_batch_size" in str(error) for error in errors)

    @staticmethod
    def test_validation_extra_search_invalid_sync_threshold():
        """Test validation error for invalid sync_threshold type"""
        with pytest.raises(ValidationError) as exc_info:
            ChromaVectorField(extra_search={"sync_threshold": "invalid"})
        errors = exc_info.value.errors()
        assert any("invalid_sync_threshold" in str(error) for error in errors)

    @staticmethod
    def test_to_dict_search():
        """Test to_dict method for search stage"""
        field = ChromaVectorField(
            max_neighbors=32,
            ef_construction=200,
            ef_search=150,
            extra_search={"num_threads": 4},
        )
        result = field.to_dict("search")
        # Search stage should include extra_search contents (unpacked)
        # Note: ef_search, max_neighbors, ef_construction are marked as IS_CONSTRUCT
        # so they won't appear in search stage results
        assert "num_threads" in result
        assert result["num_threads"] == 4
        # Search stage should not include construction-only fields
        assert "max_neighbors" not in result
        assert "ef_construction" not in result
        assert "ef_search" not in result
        # Should not include internal fields
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result
        assert "extra_search" not in result  # Should be unpacked

    @staticmethod
    def test_to_dict_construct():
        """Test to_dict method for construct stage"""
        field = ChromaVectorField(
            max_neighbors=32,
            ef_construction=200,
            ef_search=150,
            extra_search={"num_threads": 4},
        )
        result = field.to_dict("construct")
        # Construct stage should include all fields marked with IS_CONSTRUCT
        assert "max_neighbors" in result
        assert result["max_neighbors"] == 32
        assert "ef_construction" in result
        assert result["ef_construction"] == 200
        assert "ef_search" in result
        assert result["ef_search"] == 150
        # Should not include internal fields
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result
        assert "extra_construct" not in result
        assert "extra_search" not in result

    @staticmethod
    def test_to_dict_search_with_none_fields():
        """Test to_dict search stage filters out None fields"""
        field = ChromaVectorField(
            max_neighbors=32,
            ef_construction=200,
            # ef_search uses default, but it's marked as IS_CONSTRUCT so won't appear in search
        )
        result = field.to_dict("search")
        # ef_search is marked as IS_CONSTRUCT, so it won't appear in search stage
        assert "ef_search" not in result
        # Search stage only has extra_search contents if provided
        assert result == {}

    @staticmethod
    def test_to_dict_construct_with_none_fields():
        """Test to_dict construct stage filters out None fields"""
        field = ChromaVectorField(
            max_neighbors=32,
            ef_construction=200,
        )
        result = field.to_dict("construct")
        # Construction fields should be present
        assert "max_neighbors" in result
        assert "ef_construction" in result

    @staticmethod
    def test_extra_search_merged_in_to_dict():
        """Test that extra_search is properly merged in to_dict"""
        field = ChromaVectorField(
            ef_search=100,
            extra_search={"resize_factor": 2.0, "num_threads": 4},
        )
        result = field.to_dict("search")
        # Extra search params should be merged into result
        assert result["resize_factor"] == 2.0
        assert result["num_threads"] == 4
        # ef_search is marked as IS_CONSTRUCT, so it won't appear in search stage
        assert "ef_search" not in result
        # The extra_search key itself should not be present
        assert "extra_search" not in result
