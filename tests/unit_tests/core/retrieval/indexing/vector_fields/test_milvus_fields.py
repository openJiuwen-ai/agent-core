# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Milvus vector fields test cases
"""

import pytest
from pydantic import ValidationError

from openjiuwen.core.retrieval.indexing.vector_fields.milvus_fields import (
    MilvusAUTO,
    MilvusFLAT,
    MilvusHNSW,
    MilvusIVF,
    MilvusSCANN,
)


class TestMilvusFLAT:
    """Test cases for MilvusFLAT"""

    @staticmethod
    def test_init_default():
        """Test initialization with default values"""
        field = MilvusFLAT()
        assert field.vector_field == "embedding"
        assert field.database_type == "milvus"
        assert field.index_type == "flat"

    @staticmethod
    def test_init_custom_vector_field():
        """Test initialization with custom vector field name"""
        field = MilvusFLAT(vector_field="custom_embedding")
        assert field.vector_field == "custom_embedding"
        assert field.database_type == "milvus"
        assert field.index_type == "flat"

    @staticmethod
    def test_to_dict_search():
        """Test to_dict method for search stage"""
        field = MilvusFLAT(vector_field="embeddings")
        result = field.to_dict("search")
        # FLAT has no stage-specific fields, should return empty dict
        assert result == {}
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result

    @staticmethod
    def test_to_dict_construct():
        """Test to_dict method for construct stage"""
        field = MilvusFLAT(vector_field="embeddings")
        result = field.to_dict("construct")
        # FLAT has no stage-specific fields, should return empty dict
        assert result == {}
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result


class TestMilvusAUTO:
    """Test cases for MilvusAUTO"""

    @staticmethod
    def test_init_default():
        """Test initialization with default values"""
        field = MilvusAUTO()
        assert field.vector_field == "embedding"
        assert field.database_type == "milvus"
        assert field.index_type == "auto"

    @staticmethod
    def test_init_custom_vector_field():
        """Test initialization with custom vector field name"""
        field = MilvusAUTO(vector_field="custom_embedding")
        assert field.vector_field == "custom_embedding"
        assert field.database_type == "milvus"
        assert field.index_type == "auto"

    @staticmethod
    def test_to_dict_search():
        """Test to_dict method for search stage"""
        field = MilvusAUTO(vector_field="embeddings")
        result = field.to_dict("search")
        # AUTO has no stage-specific fields, should return empty dict
        assert result == {}
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result

    @staticmethod
    def test_to_dict_construct():
        """Test to_dict method for construct stage"""
        field = MilvusAUTO(vector_field="embeddings")
        result = field.to_dict("construct")
        # AUTO has no stage-specific fields, should return empty dict
        assert result == {}
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result


class TestMilvusSCANN:
    """Test cases for MilvusSCANN"""

    @staticmethod
    def test_init_default():
        """Test initialization with default values"""
        field = MilvusSCANN()
        assert field.vector_field == "embedding"
        assert field.database_type == "milvus"
        assert field.index_type == "scann"
        assert field.nlist == 128
        assert field.nprobe == 8
        assert field.with_raw_data is True
        # reorder_k has default None, should be None (not set)
        assert field.reorder_k is None

    @staticmethod
    def test_init_custom_parameters():
        """Test initialization with custom parameters"""
        field = MilvusSCANN(
            vector_field="embeddings",
            nlist=256,
            nprobe=16,
            with_raw_data=False,
            reorder_k=50,
        )
        assert field.vector_field == "embeddings"
        assert field.nlist == 256
        assert field.nprobe == 16
        assert field.with_raw_data is False
        assert field.reorder_k == 50

    @staticmethod
    def test_init_nlist_min():
        """Test initialization with minimum nlist"""
        field = MilvusSCANN(nlist=1, nprobe=1)
        assert field.nlist == 1
        assert field.nprobe == 1

    @staticmethod
    def test_init_nlist_max():
        """Test initialization with maximum nlist"""
        field = MilvusSCANN(nlist=65536)
        assert field.nlist == 65536

    @staticmethod
    def test_init_nprobe_min():
        """Test initialization with minimum nprobe"""
        field = MilvusSCANN(nprobe=1)
        assert field.nprobe == 1

    @staticmethod
    def test_init_nprobe_max():
        """Test initialization with maximum nprobe"""
        field = MilvusSCANN(nlist=65536, nprobe=65536)
        assert field.nlist == 65536
        assert field.nprobe == 65536

    @staticmethod
    def test_init_reorder_k_min():
        """Test initialization with minimum reorder_k"""
        field = MilvusSCANN(reorder_k=1)
        assert field.reorder_k == 1

    @staticmethod
    def test_validation_nlist_too_low():
        """Test validation error for nlist below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusSCANN(nlist=0)
        errors = exc_info.value.errors()
        assert any(error["type"] == "greater_than_equal" and "nlist" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_nlist_too_high():
        """Test validation error for nlist above maximum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusSCANN(nlist=65537)
        errors = exc_info.value.errors()
        assert any(error["type"] == "less_than_equal" and "nlist" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_nprobe_too_low():
        """Test validation error for nprobe below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusSCANN(nprobe=0)
        errors = exc_info.value.errors()
        assert any(error["type"] == "greater_than_equal" and "nprobe" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_nprobe_too_high():
        """Test validation error for nprobe above maximum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusSCANN(nprobe=65537)
        errors = exc_info.value.errors()
        assert any(error["type"] == "less_than_equal" and "nprobe" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_nprobe_greater_than_nlist():
        """Test validation error when nprobe > nlist"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusSCANN(nlist=64, nprobe=128)
        errors = exc_info.value.errors()
        assert any("nprobe_vs_nlist" in str(error) for error in errors)

    @staticmethod
    def test_validation_reorder_k_too_low():
        """Test validation error for reorder_k below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusSCANN(reorder_k=0)
        errors = exc_info.value.errors()
        assert any(error["type"] == "greater_than_equal" and "reorder_k" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_to_dict_search():
        """Test to_dict method for search stage"""
        field = MilvusSCANN(nlist=256, nprobe=16, reorder_k=50)
        result = field.to_dict("search")
        # Search stage should include nprobe and reorder_k
        assert "nprobe" in result
        assert result["nprobe"] == 16
        assert "reorder_k" in result
        assert result["reorder_k"] == 50
        # Search stage should not include construction-only fields
        assert "nlist" not in result
        assert "with_raw_data" not in result
        # Should not include internal fields
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result

    @staticmethod
    def test_to_dict_construct():
        """Test to_dict method for construct stage"""
        field = MilvusSCANN(nlist=256, nprobe=16, with_raw_data=False)
        result = field.to_dict("construct")
        # Construct stage should include nlist and with_raw_data
        assert "nlist" in result
        assert result["nlist"] == 256
        assert "with_raw_data" in result
        assert result["with_raw_data"] is False
        # Construct stage should not include search-only fields
        assert "nprobe" not in result
        assert "reorder_k" not in result
        # Should not include internal fields
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result

    @staticmethod
    def test_to_dict_search_without_reorder_k():
        """Test to_dict search stage when reorder_k is not set (None)"""
        field = MilvusSCANN(nlist=256, nprobe=16)
        result = field.to_dict("search")
        # reorder_k should not appear if it's None
        assert "reorder_k" not in result
        assert "nprobe" in result


class TestMilvusIVF:
    """Test cases for MilvusIVF"""

    @staticmethod
    def test_init_default():
        """Test initialization with default values"""
        field = MilvusIVF()
        assert field.vector_field == "embedding"
        assert field.database_type == "milvus"
        assert field.index_type == "ivf"
        assert field.variant == "FLAT"
        assert field.nlist == 128
        assert field.nprobe == 8
        assert field.extra_construct == {}
        assert field.extra_search == {}

    @staticmethod
    def test_init_custom_parameters():
        """Test initialization with custom parameters"""
        field = MilvusIVF(
            vector_field="embeddings",
            variant="SQ8",
            nlist=256,
            nprobe=16,
        )
        assert field.vector_field == "embeddings"
        assert field.variant == "SQ8"
        assert field.nlist == 256
        assert field.nprobe == 16

    @staticmethod
    def test_init_variant_flat():
        """Test initialization with FLAT variant"""
        field = MilvusIVF(variant="FLAT")
        assert field.variant == "FLAT"

    @staticmethod
    def test_init_variant_sq8():
        """Test initialization with SQ8 variant"""
        field = MilvusIVF(variant="SQ8")
        assert field.variant == "SQ8"

    @staticmethod
    def test_init_variant_pq():
        """Test initialization with PQ variant"""
        field = MilvusIVF(
            variant="PQ",
            extra_construct={"m": 64, "nbits": 8},
        )
        assert field.variant == "PQ"
        assert field.extra_construct["m"] == 64
        assert field.extra_construct["nbits"] == 8

    @staticmethod
    def test_init_variant_rabitq():
        """Test initialization with RABITQ variant"""
        field = MilvusIVF(
            variant="RABITQ",
            extra_construct={"refine": True, "refine_type": "SQ8"},
            extra_search={"refine_k": 1.5, "rbq_query_bits": 4},
        )
        assert field.variant == "RABITQ"
        assert field.extra_construct["refine"] is True
        assert field.extra_construct["refine_type"] == "SQ8"
        assert field.extra_search["refine_k"] == 1.5
        assert field.extra_search["rbq_query_bits"] == 4

    @staticmethod
    def test_validation_nprobe_greater_than_nlist():
        """Test validation error when nprobe > nlist"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(nlist=64, nprobe=128)
        errors = exc_info.value.errors()
        assert any("nprobe_vs_nlist" in str(error) for error in errors)

    @staticmethod
    def test_validation_flat_with_extra_args():
        """Test validation error for FLAT variant with extra arguments"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(variant="FLAT", extra_construct={"m": 64})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_sq8_with_extra_args():
        """Test validation error for SQ8 variant with extra arguments"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(variant="SQ8", extra_search={"refine_k": 1.5})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_pq_with_extra_search():
        """Test validation error for PQ variant with extra_search"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(
                variant="PQ",
                extra_construct={"m": 64, "nbits": 8},
                extra_search={"refine_k": 1.5},
            )
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_pq_invalid_m():
        """Test validation error for PQ variant with invalid m"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(variant="PQ", extra_construct={"m": 0})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_pq_invalid_nbits():
        """Test validation error for PQ variant with invalid nbits"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(variant="PQ", extra_construct={"m": 64, "nbits": 0})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_pq_nbits_too_high():
        """Test validation error for PQ variant with nbits > 24"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(variant="PQ", extra_construct={"m": 64, "nbits": 25})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_rabitq_invalid_refine_type():
        """Test validation error for RABITQ variant with invalid refine_type"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(
                variant="RABITQ",
                extra_construct={"refine": True, "refine_type": "INVALID"},
            )
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_rabitq_invalid_refine_k():
        """Test validation error for RABITQ variant with invalid refine_k"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(
                variant="RABITQ",
                extra_search={"refine_k": 0.5},
            )
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_rabitq_invalid_rbq_query_bits():
        """Test validation error for RABITQ variant with invalid rbq_query_bits"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusIVF(
                variant="RABITQ",
                extra_search={"rbq_query_bits": 9},
            )
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_to_dict_search():
        """Test to_dict method for search stage"""
        field = MilvusIVF(nlist=256, nprobe=16, variant="FLAT")
        result = field.to_dict("search")
        # Search stage should include nprobe
        assert "nprobe" in result
        assert result["nprobe"] == 16
        # Search stage should not include construction-only fields
        assert "nlist" not in result
        # Should not include internal fields
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result
        assert "variant" not in result

    @staticmethod
    def test_to_dict_construct():
        """Test to_dict method for construct stage"""
        field = MilvusIVF(nlist=256, nprobe=16, variant="FLAT")
        result = field.to_dict("construct")
        # Construct stage should include nlist
        assert "nlist" in result
        assert result["nlist"] == 256
        # Construct stage should not include search-only fields
        assert "nprobe" not in result
        # Should not include internal fields
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result
        assert "variant" not in result

    @staticmethod
    def test_to_dict_search_with_extra_search():
        """Test to_dict search stage with extra_search"""
        field = MilvusIVF(
            variant="RABITQ",
            nlist=256,
            nprobe=16,
            extra_search={"refine_k": 1.5, "rbq_query_bits": 4},
        )
        result = field.to_dict("search")
        assert "nprobe" in result
        assert result["nprobe"] == 16
        assert "refine_k" in result
        assert result["refine_k"] == 1.5
        assert "rbq_query_bits" in result
        assert result["rbq_query_bits"] == 4
        assert "extra_search" not in result  # Should be unpacked

    @staticmethod
    def test_to_dict_construct_with_extra_construct():
        """Test to_dict construct stage with extra_construct"""
        field = MilvusIVF(
            variant="PQ",
            nlist=256,
            nprobe=16,
            extra_construct={"m": 64, "nbits": 8},
        )
        result = field.to_dict("construct")
        assert "nlist" in result
        assert result["nlist"] == 256
        assert "m" in result
        assert result["m"] == 64
        assert "nbits" in result
        assert result["nbits"] == 8
        assert "extra_construct" not in result  # Should be unpacked


class TestMilvusHNSW:
    """Test cases for MilvusHNSW"""

    @staticmethod
    def test_init_default():
        """Test initialization with default values"""
        field = MilvusHNSW()
        assert field.vector_field == "embedding"
        assert field.database_type == "milvus"
        assert field.index_type == "hnsw"
        assert field.max_neighbours == 30
        assert field.ef_construction == 360
        # ef_search_factor has default None, should be None (not set)
        assert field.ef_search_factor is None
        # variant has default None, should be None (not set)
        assert field.variant is None
        assert field.extra_construct == {}
        assert field.extra_search == {}

    @staticmethod
    def test_init_custom_parameters():
        """Test initialization with custom parameters"""
        field = MilvusHNSW(
            vector_field="embeddings",
            max_neighbours=64,
            ef_construction=400,
            ef_search_factor=2.0,
        )
        assert field.vector_field == "embeddings"
        assert field.max_neighbours == 64
        assert field.ef_construction == 400
        assert field.ef_search_factor == 2.0

    @staticmethod
    def test_init_max_neighbours_min():
        """Test initialization with minimum max_neighbours"""
        field = MilvusHNSW(max_neighbours=2)
        assert field.max_neighbours == 2

    @staticmethod
    def test_init_max_neighbours_max():
        """Test initialization with maximum max_neighbours"""
        field = MilvusHNSW(max_neighbours=2048)
        assert field.max_neighbours == 2048

    @staticmethod
    def test_init_ef_construction_min():
        """Test initialization with minimum ef_construction"""
        field = MilvusHNSW(ef_construction=1)
        assert field.ef_construction == 1

    @staticmethod
    def test_init_ef_search_factor_min():
        """Test initialization with minimum ef_search_factor"""
        field = MilvusHNSW(ef_search_factor=1.0)
        assert field.ef_search_factor == 1.0

    @staticmethod
    def test_init_variant_sq():
        """Test initialization with SQ variant"""
        field = MilvusHNSW(
            variant="SQ",
            extra_construct={"sq_type": "SQ8", "refine": True, "refine_type": "FP16"},
        )
        assert field.variant == "SQ"
        assert field.extra_construct["sq_type"] == "SQ8"
        assert field.extra_construct["refine"] is True
        assert field.extra_construct["refine_type"] == "FP16"

    @staticmethod
    def test_init_variant_pq():
        """Test initialization with PQ variant"""
        field = MilvusHNSW(
            variant="PQ",
            extra_construct={"m": 64, "nbits": 8, "refine": True, "refine_type": "FP16"},
            extra_search={"refine_k": 1.5},
        )
        assert field.variant == "PQ"
        assert field.extra_construct["m"] == 64
        assert field.extra_construct["nbits"] == 8
        assert field.extra_search["refine_k"] == 1.5

    @staticmethod
    def test_init_variant_prq():
        """Test initialization with PRQ variant"""
        field = MilvusHNSW(
            variant="PRQ",
            extra_construct={"m": 64, "nbits": 8, "nrq": 4, "refine": True, "refine_type": "FP16"},
            extra_search={"refine_k": 1.5},
        )
        assert field.variant == "PRQ"
        assert field.extra_construct["m"] == 64
        assert field.extra_construct["nbits"] == 8
        assert field.extra_construct["nrq"] == 4
        assert field.extra_search["refine_k"] == 1.5

    @staticmethod
    def test_validation_max_neighbours_too_low():
        """Test validation error for max_neighbours below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(max_neighbours=1)
        errors = exc_info.value.errors()
        assert any(error["type"] == "greater_than_equal" and "max_neighbours" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_max_neighbours_too_high():
        """Test validation error for max_neighbours above maximum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(max_neighbours=2049)
        errors = exc_info.value.errors()
        assert any(error["type"] == "less_than_equal" and "max_neighbours" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_ef_construction_too_low():
        """Test validation error for ef_construction below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(ef_construction=0)
        errors = exc_info.value.errors()
        assert any(error["type"] == "greater_than_equal" and "ef_construction" in str(error["loc"]) for error in errors)

    @staticmethod
    def test_validation_ef_search_factor_too_low():
        """Test validation error for ef_search_factor below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(ef_search_factor=0.5)
        errors = exc_info.value.errors()
        assert any(
            error["type"] == "greater_than_equal" and "ef_search_factor" in str(error["loc"]) for error in errors
        )

    @staticmethod
    def test_validation_sq_invalid_sq_type():
        """Test validation error for SQ variant with invalid sq_type"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(variant="SQ", extra_construct={"sq_type": "INVALID"})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_sq_invalid_refine_type():
        """Test validation error for SQ variant with invalid refine_type"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(
                variant="SQ",
                extra_construct={"refine": True, "refine_type": "INVALID"},
            )
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_pq_invalid_m():
        """Test validation error for PQ variant with invalid m"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(variant="PQ", extra_construct={"m": 0})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_pq_invalid_nbits():
        """Test validation error for PQ variant with invalid nbits"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(variant="PQ", extra_construct={"m": 64, "nbits": 0})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_pq_invalid_refine_k():
        """Test validation error for PQ variant with invalid refine_k"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(
                variant="PQ",
                extra_search={"refine_k": 0.5},
            )
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_prq_invalid_nrq():
        """Test validation error for PRQ variant with invalid nrq"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(variant="PRQ", extra_construct={"nrq": 0})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_validation_prq_nrq_too_high():
        """Test validation error for PRQ variant with nrq > 16"""
        with pytest.raises(ValidationError) as exc_info:
            MilvusHNSW(variant="PRQ", extra_construct={"nrq": 17})
        errors = exc_info.value.errors()
        assert any("invalid_extra_args" in str(error) for error in errors)

    @staticmethod
    def test_to_dict_search():
        """Test to_dict method for search stage"""
        field = MilvusHNSW(
            max_neighbours=64,
            ef_construction=400,
            ef_search_factor=2.0,
        )
        result = field.to_dict("search")
        # Search stage should include ef_search_factor
        assert "ef_search_factor" in result
        assert result["ef_search_factor"] == 2.0
        # Search stage should not include construction-only fields
        assert "max_neighbours" not in result
        assert "ef_construction" not in result
        # Should not include internal fields
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result

    @staticmethod
    def test_to_dict_construct():
        """Test to_dict method for construct stage"""
        field = MilvusHNSW(
            max_neighbours=64,
            ef_construction=400,
            ef_search_factor=2.0,
        )
        result = field.to_dict("construct")
        # Construct stage should include max_neighbours and ef_construction
        assert "max_neighbours" in result
        assert result["max_neighbours"] == 64
        assert "ef_construction" in result
        assert result["ef_construction"] == 400
        # Construct stage should not include search-only fields
        assert "ef_search_factor" not in result
        # Should not include internal fields
        assert "database_type" not in result
        assert "index_type" not in result
        assert "vector_field" not in result

    @staticmethod
    def test_to_dict_search_without_ef_search_factor():
        """Test to_dict search stage when ef_search_factor is not set (None)"""
        field = MilvusHNSW(max_neighbours=64, ef_construction=400)
        result = field.to_dict("search")
        # ef_search_factor should not appear if it's None
        assert "ef_search_factor" not in result

    @staticmethod
    def test_to_dict_search_with_extra_search():
        """Test to_dict search stage with extra_search"""
        field = MilvusHNSW(
            variant="PQ",
            ef_search_factor=2.0,
            extra_search={"refine_k": 1.5},
        )
        result = field.to_dict("search")
        assert "ef_search_factor" in result
        assert result["ef_search_factor"] == 2.0
        assert "refine_k" in result
        assert result["refine_k"] == 1.5
        assert "extra_search" not in result  # Should be unpacked

    @staticmethod
    def test_to_dict_construct_with_extra_construct():
        """Test to_dict construct stage with extra_construct"""
        field = MilvusHNSW(
            variant="SQ",
            max_neighbours=64,
            ef_construction=400,
            extra_construct={"sq_type": "SQ8", "refine": True},
        )
        result = field.to_dict("construct")
        assert "max_neighbours" in result
        assert result["max_neighbours"] == 64
        assert "ef_construction" in result
        assert result["ef_construction"] == 400
        assert "sq_type" in result
        assert result["sq_type"] == "SQ8"
        assert "refine" in result
        assert result["refine"] is True
        assert "extra_construct" not in result  # Should be unpacked

    @staticmethod
    def test_sq_variant_sq_types():
        """Test SQ variant with all valid sq_type values"""
        for sq_type in ["SQ4U", "SQ6", "SQ8", "FP16", "BF16"]:
            field = MilvusHNSW(variant="SQ", extra_construct={"sq_type": sq_type})
            assert field.extra_construct["sq_type"] == sq_type

    @staticmethod
    def test_sq_variant_refine_types():
        """Test SQ variant with all valid refine_type values"""
        for refine_type in ["SQ6", "SQ8", "FP16", "BF16", "FP32"]:
            field = MilvusHNSW(
                variant="SQ",
                extra_construct={"refine": True, "refine_type": refine_type},
            )
            assert field.extra_construct["refine_type"] == refine_type
